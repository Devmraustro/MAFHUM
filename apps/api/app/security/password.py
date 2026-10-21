"""Password hashing with bcrypt (pyca/bcrypt, Apache-2.0).

Never store plaintext passwords. Passwords are UTF-8 SHA-256 pre-hashed (64
hex ASCII bytes, always within bcrypt's 72-byte block limit for full Arabic,
French, and Unicode passphrase support) before salted bcrypt hashing and
constant-time verification with bcrypt.checkpw.
"""

import hashlib

import bcrypt

BCRYPT_ROUNDS = 12


def _prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        _prehash(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


# Pre-computed dummy hash evaluated when an email does not exist, preventing
# email-enumeration timing side-channels during login.
_DUMMY_PASSWORD_HASH: str = hash_password("mafhum-dummy-constant-time-verification-pad")


def verify_dummy_password(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)
