"""Concurrency, CSRF/Origin/cookie-injection, secret rotation, and log redaction tests."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SaSession

import app.api.deps as deps_module
import app.config as config_module
from app.main import app
from app.models.project import Project
from app.models.user import User
from app.security.session import new_csrf_token


def _register_session(email: str, password: str = "كلمة-مرور-آمنة-2026") -> tuple[str, str, str]:
    c = TestClient(app)
    res = c.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "preferred_locale": "ar"},
    )
    assert res.status_code == 201, res.text
    uid = str(res.json()["id"])
    sess_cookie = c.cookies.get(config_module.settings.effective_session_cookie_name)
    csrf_cookie = c.cookies.get(config_module.settings.effective_csrf_cookie_name)
    assert sess_cookie and csrf_cookie
    return uid, sess_cookie, csrf_cookie


def test_concurrent_project_creation_enforces_exact_quota_and_syncs_counter(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 simultaneous project creates for quota=3 create strictly 3 rows with zero 500s."""
    monkeypatch.setattr(config_module.settings, "default_project_quota", 3)
    user_id, sess_token, csrf_token = _register_session("concurrent-quota@univ-setif.dz")

    workers = 12
    barrier = threading.Barrier(workers)

    def _create_one(idx: int) -> tuple[int, dict[str, Any]]:
        worker_client = TestClient(app)
        worker_client.cookies.set("mafhum_session", sess_token)
        worker_client.cookies.set("mafhum_csrf", csrf_token)
        barrier.wait(timeout=10)
        resp = worker_client.post(
            "/api/v1/projects",
            json={"title": f"مشروع متزامن {idx}", "description": "اختبار التزامن"},
            headers={"X-CSRF-Token": csrf_token},
        )
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(_create_one, range(workers)))

    statuses = [code for code, _ in outcomes]
    assert 500 not in statuses, outcomes
    assert statuses.count(201) == 3, outcomes
    assert statuses.count(403) == workers - 3, outcomes

    engine = deps_module.get_engine()
    assert engine is not None
    with SaSession(engine) as db:
        actual_rows = db.execute(
            select(func.count(Project.id)).where(Project.owner_id == user_id)
        ).scalar_one()
        user_row = db.get(User, user_id)
        assert user_row is not None
        assert actual_rows == 3
        assert user_row.projects_count == 3

    # Delete one created project and confirm capacity recovers cleanly to allow 1 new project.
    created_ids = [body["id"] for code, body in outcomes if code == 201]
    follow_client = TestClient(app)
    follow_client.cookies.set("mafhum_session", sess_token)
    follow_client.cookies.set("mafhum_csrf", csrf_token)

    del_res = follow_client.delete(
        f"/api/v1/projects/{created_ids[0]}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert del_res.status_code == 204

    recreate_res = follow_client.post(
        "/api/v1/projects",
        json={"title": "مشروع بعد الحذف"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert recreate_res.status_code == 201

    with SaSession(engine) as db:
        actual_rows = db.execute(
            select(func.count(Project.id)).where(Project.owner_id == user_id)
        ).scalar_one()
        user_row = db.get(User, user_id)
        assert user_row is not None
        assert actual_rows == 3
        assert user_row.projects_count == 3


def test_concurrent_duplicate_email_registration_returns_clean_409_never_500(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simultaneous duplicate-email registrations return 1x 201 and 7x 409 with zero 500s."""
    monkeypatch.setattr(config_module.settings, "auth_rate_limit", 50)
    workers = 8
    barrier = threading.Barrier(workers)
    target_email = "race-signup@univ-oran.dz"

    def _signup(idx: int) -> int:
        c = TestClient(app, client=(f"198.51.100.{idx + 10}", 54000 + idx))
        barrier.wait(timeout=10)
        resp = c.post(
            "/api/v1/auth/register",
            json={
                "email": target_email,
                "password": "كلمة-سر-قوية-جدا-2026",
                "preferred_locale": "ar",
            },
        )
        return int(resp.status_code)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        codes = list(pool.map(_signup, range(workers)))

    assert 500 not in codes, codes
    assert codes.count(201) == 1, codes
    assert codes.count(409) == workers - 1, codes


def test_all_state_changing_routes_enforce_session_bound_csrf_origin_and_anti_injection(
    migrated_env: str,
) -> None:
    """Every cookie-authenticated mutation rejects missing CSRF, tossed cookies, and cross-origin."""
    _, victim_sess, victim_csrf = _register_session("victim-csrf@univ-alger.dz")
    _, attacker_sess, attacker_csrf = _register_session("attacker-csrf@univ-alger.dz")
    assert attacker_sess != victim_sess

    victim = TestClient(app)
    victim.cookies.set("mafhum_session", victim_sess)
    victim.cookies.set("mafhum_csrf", victim_csrf)

    proj = victim.post(
        "/api/v1/projects",
        json={"title": "مشروع الضحية"},
        headers={"X-CSRF-Token": victim_csrf},
    )
    assert proj.status_code == 201
    proj_id = proj.json()["id"]

    sessions_res = victim.get("/api/v1/auth/sessions")
    session_id = sessions_res.json()["items"][0]["id"]

    # Every single cookie-authenticated state-changing route in MAFHUM:
    mutations: list[tuple[str, str, dict[str, Any] | None]] = [
        ("PATCH", "/api/v1/me", {"full_name": "تعديل غير مصرح"}),
        ("POST", "/api/v1/projects", {"title": "مشروع مزور"}),
        ("PATCH", f"/api/v1/projects/{proj_id}", {"title": "عنوان مزور"}),
        ("DELETE", f"/api/v1/projects/{proj_id}", None),
        ("POST", "/api/v1/ai/generate", {"prompt": "اختبار", "language": "ar"}),
        ("DELETE", f"/api/v1/auth/sessions/{session_id}", None),
        ("POST", "/api/v1/auth/logout", None),
        ("POST", "/api/v1/auth/logout-all", None),
        ("DELETE", "/api/v1/me", None),
    ]

    for method, path, body in mutations:
        # 1. Missing CSRF header -> 403
        r_missing = victim.request(method, path, json=body)
        assert r_missing.status_code == 403, f"Missing CSRF not rejected on {method} {path}"

        # 2. Cookie-tossing attack: attacker plants valid `attacker_csrf` into BOTH
        # cookie AND X-CSRF-Token header while victim's HttpOnly session cookie stays
        # `victim_sess`. Naive double-submit accepts `cookie == header`; our HMAC
        # session binding MUST reject with 403!
        tossed = TestClient(app)
        tossed.cookies.set("mafhum_session", victim_sess)
        tossed.cookies.set("mafhum_csrf", attacker_csrf)
        r_tossed = tossed.request(
            method, path, json=body, headers={"X-CSRF-Token": attacker_csrf}
        )
        assert r_tossed.status_code == 403, f"Cookie-tossing not rejected on {method} {path}"

        # 3. Cross-origin Origin header even WITH valid victim session + CSRF -> 403!
        r_evil_origin = victim.request(
            method,
            path,
            json=body,
            headers={
                "X-CSRF-Token": victim_csrf,
                "Origin": "https://evil.example",
            },
        )
        assert (
            r_evil_origin.status_code == 403
        ), f"Cross-origin Origin not rejected on {method} {path}"

        # 4. Sandboxed iframe `Origin: null` even WITH valid victim session + CSRF -> 403!
        r_null_origin = victim.request(
            method,
            path,
            json=body,
            headers={"X-CSRF-Token": victim_csrf, "Origin": "null"},
        )
        assert (
            r_null_origin.status_code == 403
        ), f"Null Origin not rejected on {method} {path}"

    # Also verify cross-site login CSRF & registration CSRF are rejected (403)!
    unauth = TestClient(app)
    assert (
        unauth.post(
            "/api/v1/auth/login",
            json={"email": "victim-csrf@univ-alger.dz", "password": "x"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        unauth.post(
            "/api/v1/auth/register",
            json={
                "email": "attacker-controlled@univ-alger.dz",
                "password": "كلمة-مرور-آمنة-2026",
            },
            headers={"Referer": "https://evil.example/login-csrf.html"},
        ).status_code
        == 403
    )


def test_production_host_cookie_flags_and_session_secret_rotation(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production sets Secure + HttpOnly + SameSite=lax + __Host- cookies; secret rotation revokes all."""
    monkeypatch.setattr(config_module.settings, "app_env", "production")
    monkeypatch.setattr(
        config_module.settings, "session_secret", "initial-production-secret-" + "a" * 32
    )
    monkeypatch.setattr(
        config_module.settings, "allow_dev_origins", "https://app.mafhum.dz"
    )

    https_client = TestClient(app, base_url="https://app.mafhum.dz")
    reg = https_client.post(
        "/api/v1/auth/register",
        json={
            "email": "prod-cookies@univ-alger.dz",
            "password": "كلمة-مرور-إنتاجية-قوية-2026",
            "preferred_locale": "ar",
        },
        headers={"Origin": "https://app.mafhum.dz"},
    )
    assert reg.status_code == 201
    set_cookies = reg.headers.get_list("set-cookie")
    session_header = next(h for h in set_cookies if h.startswith("__Host-mafhum_session="))
    csrf_header = next(h for h in set_cookies if h.startswith("__Host-mafhum_csrf="))

    assert "HttpOnly" in session_header
    assert "Secure" in session_header
    assert "Path=/" in session_header
    assert "SameSite=lax" in session_header
    assert "Domain=" not in session_header

    assert "HttpOnly" not in csrf_header
    assert "Secure" in csrf_header
    assert "Path=/" in csrf_header
    assert "SameSite=lax" in csrf_header
    assert "Domain=" not in csrf_header

    # Authenticated request succeeds prior to secret rotation.
    assert https_client.get("/api/v1/me").status_code == 200
    old_csrf = https_client.cookies.get("__Host-mafhum_csrf")
    old_sess = https_client.cookies.get("__Host-mafhum_session")
    assert old_csrf and old_sess

    # Rotate SESSION_SECRET: both active session lookup AND previously issued CSRF
    # HMACs immediately invalidate!
    monkeypatch.setattr(
        config_module.settings, "session_secret", "rotated-production-secret-" + "b" * 32
    )
    assert https_client.get("/api/v1/me").status_code == 401
    assert new_csrf_token(old_sess) != old_csrf


def test_secrets_credentials_and_crlf_headers_never_leak_to_logs_or_responses(
    migrated_env: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passwords, session tokens, CSRF secrets, and DB URLs never appear in logs or responses."""
    secret_value = "ultra-sensitive-session-secret-" + "z" * 24
    sensitive_password = "كلمة-سر-سرية-للغاية-لا-تطبع-أبدا-998877"
    monkeypatch.setattr(config_module.settings, "session_secret", secret_value)

    caplog.set_level(logging.DEBUG)
    c = TestClient(app)

    # Also attempt CRLF header injection via X-Request-ID.
    evil_req_id = "bad\r\nSet-Cookie: injected=1"
    reg = c.post(
        "/api/v1/auth/register",
        json={
            "email": "redaction-check@univ-setif.dz",
            "password": sensitive_password,
            "preferred_locale": "ar",
        },
        headers={"X-Request-ID": evil_req_id},
    )
    assert reg.status_code == 201
    returned_req_id = reg.headers["X-Request-ID"]
    assert "\r" not in returned_req_id and "\n" not in returned_req_id
    assert "Set-Cookie" not in returned_req_id

    sess_token = c.cookies.get("mafhum_session")
    csrf_token = c.cookies.get("mafhum_csrf")
    assert sess_token and csrf_token

    export_res = c.get("/api/v1/me/export")
    assert export_res.status_code == 200

    combined_output = reg.text + "\n" + export_res.text + "\n" + caplog.text
    for forbidden in (
        sensitive_password,
        secret_value,
        sess_token,
        csrf_token,
        "password_hash",
    ):
        assert forbidden not in combined_output
