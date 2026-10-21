"""Authentication, password hashing, cookie flags, sessions, and profile tests."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import app.config as config_module
from app.main import app


def _csrf_headers(c: TestClient) -> dict[str, str]:
    token = c.cookies.get("mafhum_csrf", "")
    return {"X-CSRF-Token": token}


def test_register_hashes_password_with_bcrypt_and_supports_long_arabic_passphrase(
    migrated_env: str,
) -> None:
    c = TestClient(app)
    long_arabic_passphrase = "عبارة مرور أكاديمية قوية للطلبة الجزائريين تتجاوز اثنين وسبعين بايتا بسهولة 2026!"
    assert len(long_arabic_passphrase.encode("utf-8")) > 72

    resp = c.post(
        "/api/v1/auth/register",
        json={
            "email": "Student.One@Univ-Setif.DZ",
            "password": long_arabic_passphrase,
            "full_name": "أمينة بوعلام",
            "preferred_locale": "ar",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "student.one@univ-setif.dz"
    assert body["full_name"] == "أمينة بوعلام"
    assert body["preferred_locale"] == "ar"
    assert "password" not in body
    assert "password_hash" not in body
    assert resp.headers.get("x-request-id")

    # Verify Set-Cookie sets HttpOnly on session cookie and SameSite=lax.
    set_cookies = resp.headers.get_list("set-cookie")
    session_header = next(h for h in set_cookies if "mafhum_session=" in h)
    assert "httponly" in session_header.lower()
    assert "samesite=lax" in session_header.lower()

    # Verify DB stores bcrypt hash ($2b$12$...) and hashed session token, never plaintext.
    raw_session_cookie = c.cookies.get("mafhum_session")
    assert raw_session_cookie
    engine = create_engine(f"sqlite:///{migrated_env}")
    with engine.connect() as conn:
        pw_hash = conn.execute(
            text("SELECT password_hash FROM users WHERE email = 'student.one@univ-setif.dz'")
        ).scalar_one()
        token_hash = conn.execute(text("SELECT token_hash FROM sessions")).scalar_one()
    engine.dispose()

    assert pw_hash != long_arabic_passphrase
    assert pw_hash.startswith("$2b$12$")
    assert token_hash != raw_session_cookie
    assert len(token_hash) == 64  # HMAC-SHA256 hex digest


def test_login_logout_and_replay_rejection(migrated_env: str) -> None:
    c = TestClient(app)
    c.post(
        "/api/v1/auth/register",
        json={"email": "karim@univ-alger.dz", "password": "StrongPassword!2026"},
    )
    captured_session = c.cookies.get("mafhum_session")
    assert captured_session

    # Logout without CSRF header must fail with 403 safe error envelope.
    no_csrf = c.post("/api/v1/auth/logout")
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "http_403"
    assert no_csrf.json()["request_id"] == no_csrf.headers.get("x-request-id")

    # Logout with valid CSRF revokes the session on the server.
    ok_logout = c.post("/api/v1/auth/logout", headers=_csrf_headers(c))
    assert ok_logout.status_code == 200

    # Even if an attacker replays the captured session cookie directly, access is denied.
    replay_client = TestClient(app)
    replay_client.cookies.set("mafhum_session", captured_session)
    replayed = replay_client.get("/api/v1/me")
    assert replayed.status_code == 401
    assert replayed.json()["code"] == "http_401"


def test_session_listing_per_device_revocation_and_expiration(migrated_env: str) -> None:
    device_1 = TestClient(app)
    device_2 = TestClient(app)

    reg = device_1.post(
        "/api/v1/auth/register",
        json={"email": "nadia@usthb.dz", "password": "NadiaSecurePassword99!"},
    )
    assert reg.status_code == 201

    log = device_2.post(
        "/api/v1/auth/login",
        json={"email": "nadia@usthb.dz", "password": "NadiaSecurePassword99!"},
    )
    assert log.status_code == 200

    sessions_resp = device_1.get("/api/v1/auth/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()["items"]
    assert len(sessions) == 2
    assert sum(1 for s in sessions if s["is_current"]) == 1
    assert all("token_hash" not in s and "token" not in s for s in sessions)

    # Revoke device_2's session from device_1; device_1 remains valid, device_2 loses access.
    other_session_id = next(s["id"] for s in sessions if not s["is_current"])
    rev = device_1.delete(
        f"/api/v1/auth/sessions/{other_session_id}",
        headers=_csrf_headers(device_1),
    )
    assert rev.status_code == 200
    assert device_2.get("/api/v1/me").status_code == 401
    assert device_1.get("/api/v1/me").status_code == 200

    # Expire device_1's remaining session in DB and prove expired sessions are rejected.
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    engine = create_engine(f"sqlite:///{migrated_env}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE sessions SET expires_at = :past"), {"past": past})
    engine.dispose()

    expired_resp = device_1.get("/api/v1/me")
    assert expired_resp.status_code == 401
    assert expired_resp.json()["code"] == "http_401"
    assert expired_resp.json()["request_id"] == expired_resp.headers.get("x-request-id")


def test_profile_update_and_privilege_escalation_rejection(migrated_env: str) -> None:
    c = TestClient(app)
    c.post(
        "/api/v1/auth/register",
        json={"email": "salim@univ-oran1.dz", "password": "SalimPassword#2026"},
    )
    upd = c.patch(
        "/api/v1/me",
        headers=_csrf_headers(c),
        json={"full_name": "سليم بن يحيى", "preferred_locale": "fr"},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["full_name"] == "سليم بن يحيى"
    assert upd.json()["preferred_locale"] == "fr"

    # Attempting to overwrite server-controlled fields is strictly rejected (422).
    escalate = c.patch(
        "/api/v1/me",
        headers=_csrf_headers(c),
        json={"project_quota": 9999},
    )
    assert escalate.status_code == 422
    assert escalate.json()["code"] == "validation_error"


def test_auth_rate_limiting_blocks_brute_force_and_abuse(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module.settings, "auth_rate_limit", 3)
    c = TestClient(app)
    c.post(
        "/api/v1/auth/register",
        json={"email": "target@univ-setif.dz", "password": "TargetPassword#2026"},
    )

    for _ in range(3):
        bad = c.post(
            "/api/v1/auth/login",
            json={"email": "target@univ-setif.dz", "password": "wrong-password"},
        )
        assert bad.status_code == 401

    throttled = c.post(
        "/api/v1/auth/login",
        json={"email": "target@univ-setif.dz", "password": "TargetPassword#2026"},
    )
    assert throttled.status_code == 429
    assert throttled.headers.get("retry-after") is not None
    body = throttled.json()
    assert body["code"] == "http_429"
    assert body["request_id"] == throttled.headers.get("x-request-id")
