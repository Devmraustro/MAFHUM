"""Phase 2 security, tenant isolation (IDOR), quota enforcement & account deletion proofs."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import app.config as config_module
from app.main import app


def _csrf_headers(c: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": c.cookies.get("mafhum_csrf", "")}


def _register_client(
    email: str, password: str = "AcademicPass#2026"
) -> tuple[TestClient, dict[str, Any]]:
    c = TestClient(app)
    resp = c.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return c, resp.json()


def test_unauthenticated_requests_cannot_access_protected_endpoints(migrated_env: str) -> None:
    anon = TestClient(app)
    anon.headers["X-User-Id"] = str(uuid.uuid4())
    anon.cookies.set("mafhum_session", "forged-non-existent-session-token")
    anon.cookies.set("mafhum_csrf", "csrf-token")
    headers = {"X-CSRF-Token": "csrf-token"}

    fake_id = str(uuid.uuid4())
    protected_calls = [
        ("GET", "/api/v1/me", None),
        ("PATCH", "/api/v1/me", {"full_name": "Attacker"}),
        ("GET", "/api/v1/me/usage", None),
        ("GET", "/api/v1/me/export", None),
        ("DELETE", "/api/v1/me", None),
        ("GET", "/api/v1/auth/me", None),
        ("GET", "/api/v1/auth/sessions", None),
        ("DELETE", f"/api/v1/auth/sessions/{fake_id}", None),
        ("POST", "/api/v1/auth/logout", None),
        ("POST", "/api/v1/auth/logout-all", None),
        ("GET", "/api/v1/projects", None),
        ("POST", "/api/v1/projects", {"title": "Unauthorized"}),
        ("GET", f"/api/v1/projects/{fake_id}", None),
        ("PATCH", f"/api/v1/projects/{fake_id}", {"title": "Hijacked"}),
        ("DELETE", f"/api/v1/projects/{fake_id}", None),
    ]

    for method, path, payload in protected_calls:
        resp = anon.request(method, path, json=payload, headers=headers)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["code"] == "http_401"
        assert body["request_id"] == resp.headers.get("x-request-id")


def test_user_a_cannot_read_modify_delete_or_spoof_user_b_projects(migrated_env: str) -> None:
    client_a, user_a = _register_client("user.a@univ-setif.dz")
    client_b, user_b = _register_client("user.b@univ-constantine.dz")

    # User B creates a private academic research project.
    create_b = client_b.post(
        "/api/v1/projects",
        headers=_csrf_headers(client_b),
        json={
            "title": "القانون الإداري الجزائري — مذكرة ماستر سرية",
            "description": "ملاحظات بحثية خاصة بالطالب ب",
        },
    )
    assert create_b.status_code == 201, create_b.text
    project_b = create_b.json()
    project_b_id = project_b["id"]
    assert project_b["owner_id"] == user_b["id"]

    # User A creates own project.
    create_a = client_a.post(
        "/api/v1/projects",
        headers=_csrf_headers(client_a),
        json={"title": "مشروع الطالب أ"},
    )
    assert create_a.status_code == 201

    # 1. List with spoofed user_id / owner_id query params & X-User-Id header still returns ONLY User A's project.
    list_a = client_a.get(
        f"/api/v1/projects?owner_id={user_b['id']}&user_id={user_b['id']}",
        headers={"X-User-Id": user_b["id"]},
    )
    assert list_a.status_code == 200
    assert list_a.json()["total"] == 1
    assert [p["owner_id"] for p in list_a.json()["items"]] == [user_a["id"]]

    # 2. Direct read of User B's copied ID returns exact same 404 envelope as a random non-existent UUID.
    random_missing = client_a.get(f"/api/v1/projects/{uuid.uuid4()}")
    read_b_by_a = client_a.get(f"/api/v1/projects/{project_b_id}")
    assert read_b_by_a.status_code == 404
    assert random_missing.status_code == 404
    assert read_b_by_a.json()["code"] == random_missing.json()["code"] == "http_404"
    assert read_b_by_a.json()["message"] == random_missing.json()["message"] == "Project not found"
    assert "مذكرة ماستر" not in read_b_by_a.text

    # 3. Direct PATCH & DELETE by User A against User B's project ID fail with 404 without altering data.
    patch_b_by_a = client_a.patch(
        f"/api/v1/projects/{project_b_id}",
        headers=_csrf_headers(client_a),
        json={"title": "تم الاختراق"},
    )
    assert patch_b_by_a.status_code == 404

    del_b_by_a = client_a.delete(
        f"/api/v1/projects/{project_b_id}",
        headers=_csrf_headers(client_a),
    )
    assert del_b_by_a.status_code == 404

    # 4. Spoofing owner_id in POST body is rejected outright (422).
    spoof_post = client_a.post(
        "/api/v1/projects",
        headers=_csrf_headers(client_a),
        json={"title": "Injected into B", "owner_id": user_b["id"]},
    )
    assert spoof_post.status_code == 422

    # Verify User B's project is completely untouched.
    intact_b = client_b.get(f"/api/v1/projects/{project_b_id}")
    assert intact_b.status_code == 200
    assert intact_b.json()["title"] == "القانون الإداري الجزائري — مذكرة ماستر سرية"
    assert intact_b.json()["description"] == "ملاحظات بحثية خاصة بالطالب ب"


def test_projects_crud_pagination_validation_and_timestamps(migrated_env: str) -> None:
    c, _user = _register_client("crud.student@univ-annaba.dz")
    headers = _csrf_headers(c)

    # Validation checks: blank title, oversized title, invalid pagination, SQLi literal safety.
    assert c.post("/api/v1/projects", headers=headers, json={"title": "   "}).status_code == 422
    assert c.post("/api/v1/projects", headers=headers, json={"title": "أ" * 201}).status_code == 422
    assert c.get("/api/v1/projects?limit=0").status_code == 422
    assert c.get("/api/v1/projects?limit=101").status_code == 422
    assert c.get("/api/v1/projects?offset=-1").status_code == 422

    created_ids: list[str] = []
    titles = [
        "مدخل إلى علم الاجتماع",
        "تحليل النصوص القانونية",
        "'; DROP TABLE projects; --",
    ]
    for title in titles:
        r = c.post("/api/v1/projects", headers=headers, json={"title": title, "description": "وصف"})
        assert r.status_code == 201, r.text
        created_ids.append(r.json()["id"])

    page1 = c.get("/api/v1/projects?limit=2&offset=0").json()
    page2 = c.get("/api/v1/projects?limit=2&offset=2").json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1

    # Update timestamps & clear optional description cleanly.
    target_id = created_ids[0]
    before = c.get(f"/api/v1/projects/{target_id}").json()
    updated = c.patch(
        f"/api/v1/projects/{target_id}",
        headers=headers,
        json={"title": "مدخل محدث إلى علم الاجتماع", "description": None},
    )
    assert updated.status_code == 200
    after = updated.json()
    assert after["title"] == "مدخل محدث إلى علم الاجتماع"
    assert after["description"] is None
    assert after["updated_at"] >= before["created_at"]

    # Delete returns 204 and subsequent GET returns 404.
    deleted = c.delete(f"/api/v1/projects/{target_id}", headers=headers)
    assert deleted.status_code == 204
    assert c.get(f"/api/v1/projects/{target_id}").status_code == 404


def test_quota_limits_enforced_server_side_and_cannot_be_bypassed(
    migrated_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module.settings, "default_project_quota", 2)
    c, _user = _register_client("quota.student@univ-blida.dz")
    headers = _csrf_headers(c)

    p1 = c.post("/api/v1/projects", headers=headers, json={"title": "المشروع الأول"})
    p2 = c.post("/api/v1/projects", headers=headers, json={"title": "المشروع الثاني"})
    assert p1.status_code == 201
    assert p2.status_code == 201

    usage = c.get("/api/v1/me/usage").json()
    assert usage == {"project_quota": 2, "projects_used": 2, "projects_remaining": 0}

    # Exceeding server-side quota fails with 403 safe error envelope.
    p3 = c.post("/api/v1/projects", headers=headers, json={"title": "المشروع الثالث"})
    assert p3.status_code == 403
    assert p3.json()["code"] == "http_403"

    # Attempted client-side quota bypasses (spoofed headers, profile tampering, new session) all fail.
    bypass_profile = c.patch("/api/v1/me", headers=headers, json={"projects_count": 0})
    assert bypass_profile.status_code == 422

    fresh_client = TestClient(app)
    fresh_client.post(
        "/api/v1/auth/login",
        json={"email": "quota.student@univ-blida.dz", "password": "AcademicPass#2026"},
    )
    still_blocked = fresh_client.post(
        "/api/v1/projects",
        headers={**_csrf_headers(fresh_client), "X-Project-Quota": "999"},
        json={"title": "محاولة تجاوز الحصة"},
    )
    assert still_blocked.status_code == 403

    # Deleting a project legitimately frees quota capacity.
    assert c.delete(f"/api/v1/projects/{p1.json()['id']}", headers=headers).status_code == 204
    p3_retry = c.post("/api/v1/projects", headers=headers, json={"title": "المشروع البديل"})
    assert p3_retry.status_code == 201


def test_account_export_and_deletion_lifecycle_preserves_other_tenants(migrated_env: str) -> None:
    client_a, user_a = _register_client("delete.me@univ-tizi.dz")
    client_a_second = TestClient(app)
    client_a_second.post(
        "/api/v1/auth/login",
        json={"email": "delete.me@univ-tizi.dz", "password": "AcademicPass#2026"},
    )
    client_b, user_b = _register_client("keep.me@univ-oran1.dz")

    # Both users own projects.
    client_a.post(
        "/api/v1/projects",
        headers=_csrf_headers(client_a),
        json={"title": "مشروع أ للحذف"},
    )
    client_b.post(
        "/api/v1/projects",
        headers=_csrf_headers(client_b),
        json={"title": "مشروع ب المحفوظ"},
    )

    # Export returns User A's profile + projects + retention disclosure without secret fields.
    exported = client_a.get("/api/v1/me/export")
    assert exported.status_code == 200
    export_body = exported.json()
    assert export_body["user"]["id"] == user_a["id"]
    assert "password_hash" not in export_body["user"]
    assert len(export_body["projects"]) == 1
    assert "backups" in export_body["retention_notice"].lower()

    # Delete account requires CSRF, then removes User A + all sessions + all projects cleanly.
    assert client_a.delete("/api/v1/me").status_code == 403
    del_resp = client_a.delete("/api/v1/me", headers=_csrf_headers(client_a))
    assert del_resp.status_code == 200, del_resp.text
    del_body = del_resp.json()
    assert del_body["deleted_user_id"] == user_a["id"]
    assert del_body["sessions_revoked"] >= 2
    assert del_body["projects_deleted"] == 1
    assert "backups" in del_body["retention_notice"].lower()

    # Both prior sessions of User A immediately fail with 401.
    assert client_a.get("/api/v1/me").status_code == 401
    assert client_a_second.get("/api/v1/me").status_code == 401

    # Direct SQL verification: zero rows remain for User A; User B remains 100% intact.
    engine = create_engine(f"sqlite:///{migrated_env}")
    with engine.connect() as conn:
        a_users = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE id = :uid"), {"uid": user_a["id"]}
        ).scalar_one()
        a_sessions = conn.execute(
            text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_a["id"]}
        ).scalar_one()
        a_projects = conn.execute(
            text("SELECT COUNT(*) FROM projects WHERE owner_id = :uid"), {"uid": user_a["id"]}
        ).scalar_one()
        b_projects = conn.execute(
            text("SELECT COUNT(*) FROM projects WHERE owner_id = :uid"), {"uid": user_b["id"]}
        ).scalar_one()
    engine.dispose()

    assert (a_users, a_sessions, a_projects) == (0, 0, 0)
    assert b_projects == 1
