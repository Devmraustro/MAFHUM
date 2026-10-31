"""Authenticated field-encryption primitives — DESIGN ONLY, NOT ENABLED.

.. warning::

   Nothing in MAFHUM calls this module at runtime. No database column is
   encrypted, no key is loaded from the environment, and no production key
   management exists. This is a *testable interface* plus a reference
   implementation so the design in ``docs/ADR/0006-encryption-at-rest.md`` can be
   verified with synthetic keys before an owner-supplied KMS/HSM is chosen.

Why an envelope format instead of raw AES-GCM
---------------------------------------------
A ciphertext must say which key encrypted it, or key rotation requires a
migration that rewrites every row inside one transaction. The token format here
carries an explicit, non-secret ``key_id`` plus a fresh random nonce:

    b"MFE1" | key_id_len (1 byte) | key_id | nonce (12 bytes) | AESGCM(ct|tag)

AES-GCM is an AEAD: ``aad`` is authenticated but NOT stored in the token. Callers
pass ``aad_for(table, column, row_id)`` so a ciphertext cannot be moved to a
different row, column or table and still decrypt. That binding is verified by the
decrypt call, not by application-level convention.

Rotation is decrypt-with-any-registered-key, re-encrypt-with-primary. The
primary key is always the first key given to the constructor.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"MFE1"
_NONCE_BYTES = 12
_MAX_KEY_ID_BYTES = 64
_MAX_TOKEN_BYTES = 64 * 1024 * 1024


class EncryptionError(Exception):
    """Base class for encryption failures. Never carries key material."""


class DecryptionError(EncryptionError):
    """Ciphertext is malformed, tampered with, bound to different AAD or unknown key."""


@runtime_checkable
class FieldEncryptor(Protocol):
    """The contract any production implementation (KMS-backed or local) must meet."""

    @property
    def key_ids(self) -> tuple[str, ...]:
        """Registered key ids, primary first."""

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> str:
        """Return a self-describing, URL-safe token."""

    def decrypt(self, token: str, *, aad: bytes) -> bytes:
        """Return the plaintext, or raise ``DecryptionError``."""

    def rotate(self, token: str, *, aad: bytes) -> str:
        """Re-encrypt under the primary key; idempotent when already current."""


@dataclass(frozen=True, slots=True)
class KeySpec:
    """One encryption key. ``material`` must be 16, 24 or 32 raw bytes."""

    key_id: str
    material: bytes

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id.encode("utf-8")) > _MAX_KEY_ID_BYTES:
            raise ValueError("key_id must be 1..64 UTF-8 bytes")
        if len(self.material) not in (16, 24, 32):
            raise ValueError("AES-GCM key material must be 16, 24 or 32 bytes")


def aad_for(table: str, column: str, row_id: str) -> bytes:
    """Canonical associated data binding a ciphertext to one cell of one row.

    NUL separators make the encoding unambiguous: ``("ab","c",...)`` cannot
    collide with ``("a","bc",...)``.
    """
    if any("\x00" in part for part in (table, column, row_id)):
        raise ValueError("NUL is not allowed in AAD components")
    return f"{table}\x00{column}\x00{row_id}".encode()


def generate_synthetic_key(key_id: str = "synthetic-1") -> KeySpec:
    """Test helper. NEVER use this to create a production key."""
    return KeySpec(key_id=key_id, material=secrets.token_bytes(32))


class EnvelopeFieldEncryptor:
    """Reference AEAD implementation. Keys are supplied by the caller.

    This class deliberately knows nothing about ``Settings``, environment
    variables, files or a KMS. Production key loading is a separate, owner-owned
    decision (see the ADR); passing a key here is the only way to use it.
    """

    def __init__(self, keys: tuple[KeySpec, ...] | list[KeySpec]) -> None:
        if not keys:
            raise ValueError("at least one key is required")
        ids = [key.key_id for key in keys]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate key_id")
        self._keys = tuple(keys)
        self._by_id = {key.key_id: AESGCM(key.material) for key in keys}

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(key.key_id for key in self._keys)

    @property
    def primary_key_id(self) -> str:
        return self._keys[0].key_id

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> str:
        key_id = self.primary_key_id.encode("utf-8")
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._by_id[self.primary_key_id].encrypt(nonce, plaintext, aad)
        raw = _MAGIC + bytes([len(key_id)]) + key_id + nonce + ciphertext
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def decrypt(self, token: str, *, aad: bytes) -> bytes:
        raw = self._decode(token)
        key_id, nonce, ciphertext = _parse(raw)
        cipher = self._by_id.get(key_id)
        if cipher is None:
            raise DecryptionError("unknown key id")
        try:
            return cipher.decrypt(nonce, ciphertext, aad)
        except (InvalidTag, ValueError) as exc:
            raise DecryptionError("ciphertext failed authentication") from exc

    def rotate(self, token: str, *, aad: bytes) -> str:
        plaintext = self.decrypt(token, aad=aad)
        return self.encrypt(plaintext, aad=aad)

    def encrypted_with(self, token: str) -> str:
        """Return the key id a token claims, WITHOUT authenticating it.

        Safe for rotation planning; never use the result as an authorization
        decision.
        """
        return _parse(self._decode(token))[0]

    @staticmethod
    def _decode(token: str) -> bytes:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_BYTES:
            raise DecryptionError("invalid token size")
        try:
            return base64.urlsafe_b64decode(token.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise DecryptionError("invalid token encoding") from exc


def _parse(raw: bytes) -> tuple[str, bytes, bytes]:
    header = len(_MAGIC) + 1
    if len(raw) < header + _NONCE_BYTES + 16 or raw[: len(_MAGIC)] != _MAGIC:
        raise DecryptionError("unrecognized token header")
    key_id_len = raw[len(_MAGIC)]
    end = header + key_id_len
    if end + _NONCE_BYTES + 16 > len(raw):
        raise DecryptionError("truncated token")
    try:
        key_id = raw[header:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptionError("invalid key id") from exc
    nonce = raw[end : end + _NONCE_BYTES]
    return key_id, nonce, raw[end + _NONCE_BYTES :]


__all__ = [
    "DecryptionError",
    "EncryptionError",
    "EnvelopeFieldEncryptor",
    "FieldEncryptor",
    "KeySpec",
    "aad_for",
    "generate_synthetic_key",
]
