"""Security tests for IP-independent login rate limiting & trusted proxy parsing."""

import re

import pytest
from fastapi.testclient import TestClient

import app.api.deps as deps_module
import app.config as config_module
from app.main import app
from app.security.rate_limit import account_limiter_key, normalize_account_identifier


def test_account_rate_limit_blocks_ip_rotation_without_storing_email_pii(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating client socket IPs and X-Forwarded-For headers NEVER resets account limit."""
    monkeypatch.setattr(config_module.settings, "auth_rate_limit", 5)
    monkeypatch.setattr(config_module.settings, "auth_ip_rate_limit", 20)

    target_email = "victim.student@univ-setif.dz"
    other_email = "other.student@univ-alger.dz"
    good_password = "كلمة-سر-جامعية-قوية-2026"

    setup_client = TestClient(app, client=("198.51.100.200", 50000))
    assert (
        setup_client.post(
            "/api/v1/auth/register",
            json={"email": target_email, "password": good_password, "preferred_locale": "ar"},
        ).status_code
        == 201
    )
    assert (
        setup_client.post(
            "/api/v1/auth/register",
            json={"email": other_email, "password": good_password, "preferred_locale": "ar"},
        ).status_code
        == 201
    )
    deps_module.reset_all_limiters()

    # Attack the target account from 5 distinct client socket IPs AND spoofed headers,
    # even varying Unicode NFKC width and casing on attempt 5.
    variants = [
        target_email,
        "VICTIM.STUDENT@UNIV-SETIF.DZ",
        "  victim.student@univ-setif.dz  ",
        "Ｖictim.student@univ-setif.dz",
        target_email,
    ]
    for idx, email_variant in enumerate(variants, start=1):
        attacker = TestClient(app, client=(f"198.51.100.{idx}", 51000 + idx))
        res = attacker.post(
            "/api/v1/auth/login",
            json={"email": email_variant, "password": "wrong-password-attempt"},
            headers={
                "X-Forwarded-For": f"203.0.113.{idx}",
                "X-Real-IP": f"192.0.2.{idx}",
                "X-Request-ID": f"req-rot-{idx}",
            },
        )
        assert res.status_code == 401

    # 6th attempt from a brand-new client IP MUST hit 429 on the target account!
    sixth_attacker = TestClient(app, client=("198.51.100.99", 51099))
    blocked = sixth_attacker.post(
        "/api/v1/auth/login",
        json={"email": target_email, "password": good_password},
        headers={
            "X-Forwarded-For": "203.0.113.99",
            "X-Request-ID": "req-rot-blocked-6",
        },
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-Request-ID"] == "req-rot-blocked-6"
    body = blocked.json()
    assert body["code"] == "http_429"
    assert body["request_id"] == "req-rot-blocked-6"
    assert target_email not in blocked.text

    # An unrelated student account logging in from another clean IP succeeds immediately!
    legit_other = TestClient(app, client=("198.51.100.150", 51150))
    ok = legit_other.post(
        "/api/v1/auth/login",
        json={"email": other_email, "password": good_password},
    )
    assert ok.status_code == 200

    # Inspect internal limiter keys: strictly `acct:<64-hex-hmac>`, zero raw email PII!
    acct_keys = deps_module.login_account_limiter.keys_snapshot()
    assert len(acct_keys) == 2
    for key in acct_keys:
        assert re.fullmatch(r"acct:[0-9a-f]{64}", key), key
        assert "victim" not in key
        assert "univ-setif" not in key
        assert "@" not in key

    # Confirm deterministic NFKC + case + whitespace canonicalization.
    assert normalize_account_identifier("  Ａmina@Univ-Setif.DZ ") == "amina@univ-setif.dz"
    assert account_limiter_key("  Ａmina@Univ-Setif.DZ ", "sec") == account_limiter_key(
        "amina@univ-setif.dz", "sec"
    )


def test_ip_rate_limit_blocks_password_spraying_across_accounts_and_ignores_spoofed_xff(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single IP spraying distinct accounts hits IP limiter even when spoofing XFF."""
    monkeypatch.setattr(config_module.settings, "auth_rate_limit", 5)
    monkeypatch.setattr(config_module.settings, "auth_ip_rate_limit", 4)
    monkeypatch.setattr(config_module.settings, "trusted_proxy_ips", "")

    sprayer = TestClient(app, client=("203.0.113.77", 52000))
    for i in range(4):
        res = sprayer.post(
            "/api/v1/auth/login",
            json={
                "email": f"spray-target-{i}@univ-constantine.dz",
                "password": "wrong-password-1234",
            },
            # Attempt to spoof a fresh X-Forwarded-For on every single request:
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
        )
        assert res.status_code == 401

    # 5th sprayed account from the same socket IP is blocked despite spoofed XFF!
    fifth = sprayer.post(
        "/api/v1/auth/login",
        json={
            "email": "spray-target-99@univ-constantine.dz",
            "password": "wrong-password-1234",
        },
        headers={"X-Forwarded-For": "198.51.100.250"},
    )
    assert fifth.status_code == 429
    assert int(fifth.headers["Retry-After"]) >= 1

    # Another legitimate user on a different socket IP is completely unaffected.
    clean_client = TestClient(app, client=("203.0.113.88", 52001))
    clean_res = clean_client.post(
        "/api/v1/auth/login",
        json={
            "email": "legit-fresh@univ-constantine.dz",
            "password": "wrong-password-1234",
        },
    )
    assert clean_res.status_code == 401


def test_trusted_proxy_cidr_parses_rightmost_untrusted_hop_and_resists_left_spoofing(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusted proxy CIDR honors rightmost untrusted XFF hop & resists left prepending."""
    monkeypatch.setattr(config_module.settings, "auth_rate_limit", 10)
    monkeypatch.setattr(config_module.settings, "auth_ip_rate_limit", 2)
    monkeypatch.setattr(config_module.settings, "trusted_proxy_ips", "10.0.0.0/8")

    ingress_proxy = TestClient(app, client=("10.1.2.3", 53000))

    # Real client is 198.51.100.42; attacker prepends random left-hand IPs on each hit.
    r1 = ingress_proxy.post(
        "/api/v1/auth/login",
        json={"email": "u1@univ.dz", "password": "wrong-password-1234"},
        headers={"X-Forwarded-For": "1.1.1.1, 198.51.100.42, 10.9.9.9"},
    )
    r2 = ingress_proxy.post(
        "/api/v1/auth/login",
        json={"email": "u2@univ.dz", "password": "wrong-password-1234"},
        headers={"X-Forwarded-For": "8.8.8.8, 198.51.100.42, 10.9.9.9"},
    )
    r3 = ingress_proxy.post(
        "/api/v1/auth/login",
        json={"email": "u3@univ.dz", "password": "wrong-password-1234"},
        headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.42, 10.9.9.9"},
    )
    assert r1.status_code == 401
    assert r2.status_code == 401
    # Right-to-left scan skipped trusted 10.9.9.9 and pinned real client 198.51.100.42!
    assert r3.status_code == 429

    # A different real client arriving through the SAME ingress proxy (`10.1.2.3`) succeeds!
    other_behind_same_proxy = ingress_proxy.post(
        "/api/v1/auth/login",
        json={"email": "u4@univ.dz", "password": "wrong-password-1234"},
        headers={"X-Forwarded-For": "203.0.113.200, 10.9.9.9"},
    )
    assert other_behind_same_proxy.status_code == 401
