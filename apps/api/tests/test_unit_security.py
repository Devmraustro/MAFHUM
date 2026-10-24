"""Unit tests for password hashing, session tokens and origin verification."""

from __future__ import annotations

from typing import ClassVar

import pytest

from mafhum_api.core.security import (
    MIN_PASSWORD_LENGTH,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_session_token,
    tokens_match,
    verify_origin,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        stored = hash_password("correct-horse-battery")
        assert "correct-horse-battery" not in stored

    def test_same_password_different_hash(self) -> None:
        assert hash_password("same-password-1") != hash_password("same-password-1")

    def test_verify_correct_password(self) -> None:
        stored = hash_password("correct-horse-battery")
        assert verify_password("correct-horse-battery", stored) is True

    def test_verify_wrong_password(self) -> None:
        stored = hash_password("correct-horse-battery")
        assert verify_password("wrong-horse-battery", stored) is False

    def test_rejects_short_password(self) -> None:
        with pytest.raises(ValueError):
            hash_password("short")

    def test_rejects_overlong_password(self) -> None:
        with pytest.raises(ValueError):
            hash_password("x" * 1000)

    def test_verify_rejects_overlong_password(self) -> None:
        stored = hash_password("correct-horse-battery")
        assert verify_password("y" * 1000, stored) is False

    @pytest.mark.parametrize(
        "stored",
        [
            "",
            "garbage",
            "bcrypt$2b$12$abc",
            "scrypt$notanumber$8$1$aa$bb",
            "scrypt$16384$8$1$zz$yy",
        ],
    )
    def test_malformed_hashes_never_verify(self, stored: str) -> None:
        assert verify_password("any-password", stored) is False

    def test_minimum_length_constant_matches(self) -> None:
        assert MIN_PASSWORD_LENGTH == 8
        assert verify_password("12345678", hash_password("12345678")) is True


class TestSessionTokens:
    def test_tokens_are_unique(self) -> None:
        assert len({generate_session_token() for _ in range(200)}) == 200

    def test_hash_is_deterministic(self) -> None:
        token = generate_session_token()
        assert hash_session_token(token) == hash_session_token(token)

    def test_hash_hides_token(self) -> None:
        token = generate_session_token()
        digest = hash_session_token(token)
        assert token not in digest
        assert len(digest) == 64

    def test_different_tokens_different_hashes(self) -> None:
        assert hash_session_token(generate_session_token()) != hash_session_token(
            generate_session_token()
        )

    def test_csrf_tokens_are_unique(self) -> None:
        assert len({generate_csrf_token() for _ in range(200)}) == 200

    def test_tokens_match_is_exact(self) -> None:
        token = generate_csrf_token()
        assert tokens_match(token, token) is True
        assert tokens_match(token, token + "x") is False
        assert tokens_match("", token) is False


class TestOriginVerification:
    allowed: ClassVar[list[str]] = ["http://localhost:3000", "https://mafhum.example"]

    def test_matching_origin_allowed(self) -> None:
        assert verify_origin("http://localhost:3000", None, self.allowed).allowed

    def test_trailing_slash_normalized(self) -> None:
        assert verify_origin("http://localhost:3000/", None, self.allowed).allowed

    def test_foreign_origin_rejected(self) -> None:
        verdict = verify_origin("https://evil.example", None, self.allowed)
        assert not verdict.allowed
        assert verdict.reason == "origin_not_allowed"

    def test_subdomain_spoof_rejected(self) -> None:
        assert not verify_origin("https://mafhum.example.evil", None, self.allowed).allowed

    def test_referer_fallback_when_origin_absent(self) -> None:
        assert verify_origin(None, "http://localhost:3000/app/documents", self.allowed).allowed

    def test_referer_query_string_ignored(self) -> None:
        assert verify_origin(None, "http://localhost:3000/app?x=1", self.allowed).allowed

    def test_missing_both_rejected(self) -> None:
        verdict = verify_origin(None, None, self.allowed)
        assert not verdict.allowed
        assert verdict.reason == "missing_origin"

    def test_null_origin_rejected(self) -> None:
        assert not verify_origin("null", None, self.allowed).allowed
