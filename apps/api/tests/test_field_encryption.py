"""Synthetic-key verification of the encryption design interfaces.

No production key exists anywhere in this repository. Every key in this module
is generated in memory at test time.
"""

from __future__ import annotations

import base64

import pytest

from mafhum_api.core.crypto import (
    DecryptionError,
    EnvelopeFieldEncryptor,
    FieldEncryptor,
    KeySpec,
    aad_for,
    generate_synthetic_key,
)


def _keys() -> tuple[KeySpec, KeySpec]:
    return generate_synthetic_key("key-2026-01"), generate_synthetic_key("key-2026-10")


class TestRoundTrip:
    def test_round_trip_with_bound_aad(self) -> None:
        old, _ = _keys()
        encryptor = EnvelopeFieldEncryptor([old])
        aad = aad_for("documents", "notes", "00000000-0000-0000-0000-000000000001")
        token = encryptor.encrypt("ملاحظات خاصة".encode(), aad=aad)
        assert token != "ملاحظات خاصة".encode()
        assert encryptor.decrypt(token, aad=aad) == "ملاحظات خاصة".encode()

    def test_interface_is_satisfied(self) -> None:
        old, _ = _keys()
        assert isinstance(EnvelopeFieldEncryptor([old]), FieldEncryptor)

    def test_aad_encoding_is_unambiguous(self) -> None:
        assert aad_for("ab", "c", "1") != aad_for("a", "bc", "1")
        with pytest.raises(ValueError):
            aad_for("bad\x00table", "column", "1")


class TestTamperResistance:
    def test_wrong_aad_is_rejected(self) -> None:
        old, _ = _keys()
        encryptor = EnvelopeFieldEncryptor([old])
        token = encryptor.encrypt(b"secret", aad=aad_for("t", "c", "row-1"))
        for other in (aad_for("t", "c", "row-2"), aad_for("other", "c", "row-1"), b""):
            with pytest.raises(DecryptionError):
                encryptor.decrypt(token, aad=other)

    def test_modified_ciphertext_is_rejected(self) -> None:
        old, _ = _keys()
        encryptor = EnvelopeFieldEncryptor([old])
        aad = aad_for("t", "c", "row-1")
        token = encryptor.encrypt(b"secret", aad=aad)
        raw = bytearray(base64.urlsafe_b64decode(token))
        raw[-1] ^= 0x01
        with pytest.raises(DecryptionError):
            encryptor.decrypt(base64.urlsafe_b64encode(bytes(raw)).decode(), aad=aad)

    def test_truncated_and_garbage_tokens_are_rejected(self) -> None:
        old, _ = _keys()
        encryptor = EnvelopeFieldEncryptor([old])
        aad = aad_for("t", "c", "row-1")
        token = encryptor.encrypt(b"secret", aad=aad)
        for broken in (token[:10], "!!!not-base64!!!", "", "AAAA"):
            with pytest.raises(DecryptionError):
                encryptor.decrypt(broken, aad=aad)

    def test_unknown_key_id_is_rejected(self) -> None:
        old, new = _keys()
        aad = aad_for("t", "c", "row-1")
        token = EnvelopeFieldEncryptor([old]).encrypt(b"secret", aad=aad)
        with pytest.raises(DecryptionError, match="unknown key id"):
            EnvelopeFieldEncryptor([new]).decrypt(token, aad=aad)


class TestKeyRotation:
    def test_old_key_decrypts_and_rotation_reencrypts_under_primary(self) -> None:
        old, new = _keys()
        aad = aad_for("documents", "notes", "row-42")
        legacy_token = EnvelopeFieldEncryptor([old]).encrypt(b"payload", aad=aad)

        # Dual-read window: both keys registered, NEW key first so it is primary.
        dual = EnvelopeFieldEncryptor([new, old])
        assert dual.key_ids == ("key-2026-10", "key-2026-01")
        assert dual.decrypt(legacy_token, aad=aad) == b"payload"

        rotated = dual.rotate(legacy_token, aad=aad)
        assert rotated != legacy_token
        assert dual.encrypted_with(rotated) == "key-2026-10"
        assert EnvelopeFieldEncryptor([new]).decrypt(rotated, aad=aad) == b"payload"
        with pytest.raises(DecryptionError):
            EnvelopeFieldEncryptor([old]).decrypt(rotated, aad=aad)

    def test_rotation_is_idempotent_once_on_the_primary_key(self) -> None:
        old, new = _keys()
        dual = EnvelopeFieldEncryptor([new, old])
        aad = aad_for("t", "c", "r")
        current = dual.encrypt(b"data", aad=aad)
        assert dual.decrypt(dual.rotate(current, aad=aad), aad=aad) == b"data"
        # A fresh nonce means the bytes differ, but the key id stays primary.
        assert dual.encrypted_with(dual.rotate(current, aad=aad)) == "key-2026-10"

    def test_rotation_preserves_the_aad_binding(self) -> None:
        old, new = _keys()
        dual = EnvelopeFieldEncryptor([new, old])
        aad = aad_for("t", "c", "r")
        token = EnvelopeFieldEncryptor([old]).encrypt(b"data", aad=aad)
        rotated = dual.rotate(token, aad=aad)
        with pytest.raises(DecryptionError):
            dual.decrypt(rotated, aad=aad_for("t", "c", "other-row"))


class TestDesignIsNotAccidentallyEnabled:
    def test_no_key_material_is_read_from_settings(self) -> None:
        from mafhum_api.config import Settings

        suspicious = [name for name in Settings.model_fields if "encrypt" in name or "kms" in name]
        assert suspicious == [], f"encryption wiring requires the ADR first: {suspicious}"

    def test_main_does_not_wire_the_encryptor(self) -> None:
        from pathlib import Path

        main_source = (
            Path(__file__).resolve().parents[1] / "src" / "mafhum_api" / "main.py"
        ).read_text(encoding="utf-8")
        assert "core.crypto" not in main_source
        assert "EnvelopeFieldEncryptor" not in main_source
