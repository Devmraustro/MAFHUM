"""Authentication, session and project endpoint tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import AuthedClient


class TestHealth:
    def test_live(self, client: TestClient) -> None:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_ready_reports_checks(self, client: TestClient) -> None:
        response = client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["storage"] == "ok"

    def test_no_secrets_in_health(self, client: TestClient) -> None:
        body = client.get("/health/ready").text
        for forbidden in ("change_me", "postgresql://", "SECRET", "password"):
            assert forbidden not in body

    def test_security_headers_present(self, client: TestClient) -> None:
        headers = client.get("/health/live").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]


class TestRegistration:
    def test_register_returns_user_and_sets_cookies(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": "long-enough-password"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["user"]["email"] == "new@example.com"
        assert "mafhum_session" in response.cookies
        assert "mafhum_csrf" in response.cookies

    def test_session_cookie_is_httponly(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "flags@example.com", "password": "long-enough-password"},
            headers={"Origin": "http://testserver"},
        )
        cookie_header = response.headers.get_list("set-cookie")
        session = next(h for h in cookie_header if h.startswith("mafhum_session")).lower()
        assert "httponly" in session
        assert "samesite=lax" in session
        # The CSRF cookie must be script-readable for the double-submit pattern.
        csrf = next(h for h in cookie_header if h.startswith("mafhum_csrf")).lower()
        assert "httponly" not in csrf

    def test_duplicate_email_conflicts(self, client: TestClient) -> None:
        payload = {"email": "dupe@example.com", "password": "long-enough-password"}
        first = client.post(
            "/api/v1/auth/register", json=payload, headers={"Origin": "http://testserver"}
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/auth/register", json=payload, headers={"Origin": "http://testserver"}
        )
        assert second.status_code == 409
        assert second.json()["code"] == "conflict"

    def test_short_password_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "short@example.com", "password": "abc"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422

    def test_invalid_email_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "long-enough-password"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422

    def test_error_envelope_shape(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "bad", "password": "x"},
            headers={"Origin": "http://testserver"},
        )
        body = response.json()
        assert body["code"] == "validation_error"
        assert "request_id" in body
        assert body["field_errors"]

    def test_password_not_echoed_in_errors(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "bad", "password": "super-secret-value"},
            headers={"Origin": "http://testserver"},
        )
        assert "super-secret-value" not in response.text


class TestLogin:
    def test_login_success(self, client: TestClient, user_a: AuthedClient) -> None:
        client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
        response = client.post(
            "/api/v1/auth/login",
            json={"email": user_a.email, "password": user_a.password},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["id"] == str(user_a.user_id)

    def test_wrong_password_rejected(self, client: TestClient, user_a: AuthedClient) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": user_a.email, "password": "definitely-wrong-password"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    def test_unknown_email_same_error_as_wrong_password(
        self, client: TestClient, user_a: AuthedClient
    ) -> None:
        unknown = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "definitely-wrong-password"},
            headers={"Origin": "http://testserver"},
        )
        wrong = client.post(
            "/api/v1/auth/login",
            json={"email": user_a.email, "password": "definitely-wrong-password"},
            headers={"Origin": "http://testserver"},
        )
        # Identical status, code and message: the endpoint is not an enumerator.
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["code"] == wrong.json()["code"]
        assert unknown.json()["message"] == wrong.json()["message"]

    def test_rate_limited_after_repeated_failures(
        self, client: TestClient, user_a: AuthedClient
    ) -> None:
        last = None
        for _ in range(12):
            last = client.post(
                "/api/v1/auth/login",
                json={"email": user_a.email, "password": "wrong-password-attempt"},
                headers={"Origin": "http://testserver"},
            )
        assert last is not None
        assert last.status_code == 429
        assert last.json()["code"] == "rate_limited"


class TestSession:
    def test_me_requires_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    def test_me_returns_current_user(self, user_a: AuthedClient) -> None:
        response = user_a.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["id"] == str(user_a.user_id)

    def test_logout_invalidates_session(self, client: TestClient, user_a: AuthedClient) -> None:
        assert user_a.get("/api/v1/auth/me").status_code == 200
        assert user_a.post("/api/v1/auth/logout").status_code == 204
        assert user_a.get("/api/v1/auth/me").status_code == 401

    def test_logout_response_has_no_body(self, user_a: AuthedClient) -> None:
        """Regression: a 204 carrying a body desynchronises HTTP keep-alive.

        `JSONResponse(status_code=204, content=None)` renders ``b"null"``, so the
        server wrote 4 body bytes for a status that forbids them. Over a real
        keep-alive connection the *next* response then failed with
        "Response content longer than Content-Length" and the client saw a 500
        or a reset connection. `TestClient` does not reuse a raw socket, so this
        was only visible against a running server.
        """
        response = user_a.post("/api/v1/auth/logout")
        assert response.status_code == 204
        assert response.content == b""
        assert (
            "content-length" not in {k.lower() for k in response.headers}
            or response.headers["content-length"] == "0"
        )
        # The connection is still usable for a following request.
        assert user_a.get("/api/v1/auth/me").status_code == 401

    def test_forged_session_token_rejected(self, client: TestClient) -> None:
        client.cookies.set("mafhum_session", "forged-token-value")
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_csrf_token_endpoint(self, user_a: AuthedClient) -> None:
        response = user_a.get("/api/v1/auth/session")
        assert response.status_code == 200
        assert len(response.json()["csrf_token"]) > 20


class TestCsrfAndOrigin:
    def test_anonymous_mutation_rejected_before_csrf(self, client: TestClient) -> None:
        # Authentication is evaluated first, so an anonymous caller gets 401.
        response = client.post(
            "/api/v1/projects",
            json={"title": "بدون جلسة"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 401

    def test_mutation_without_csrf_header_rejected(self, user_a: AuthedClient) -> None:
        response = user_a._client.post(
            "/api/v1/projects",
            json={"title": "بدون رمز"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "csrf_failed"

    def test_mutation_with_wrong_csrf_rejected(self, user_a: AuthedClient) -> None:
        response = user_a._client.post(
            "/api/v1/projects",
            json={"title": "رمز خاطئ"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": "wrong-token"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "csrf_failed"

    def test_mutation_from_foreign_origin_rejected(self, user_a: AuthedClient) -> None:
        response = user_a._client.post(
            "/api/v1/projects",
            json={"title": "أصل غريب"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": user_a.csrf_token},
        )
        assert response.status_code == 422
        assert response.json()["field_errors"]["csrf"] == ["origin_rejected"]

    def test_mutation_without_origin_or_referer_rejected(self, user_a: AuthedClient) -> None:
        response = user_a._client.post(
            "/api/v1/projects",
            json={"title": "بدون أصل"},
            headers={"X-CSRF-Token": user_a.csrf_token},
        )
        assert response.status_code == 422
        assert response.json()["field_errors"]["csrf"] == ["origin_rejected"]


class TestProjects:
    def test_create_and_list(self, user_a: AuthedClient) -> None:
        created = user_a.post("/api/v1/projects", json={"title": "بحث التخرج", "language": "ar"})
        assert created.status_code == 201, created.text
        listed = user_a.get("/api/v1/projects")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["title"] == "بحث التخرج"

    def test_empty_title_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.post("/api/v1/projects", json={"title": "   "})
        assert response.status_code == 422

    def test_get_by_id(self, user_a: AuthedClient) -> None:
        project_id = user_a.post("/api/v1/projects", json={"title": "مشروع"}).json()["id"]
        response = user_a.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200
        assert response.json()["id"] == project_id

    def test_delete(self, user_a: AuthedClient) -> None:
        project_id = user_a.post("/api/v1/projects", json={"title": "للحذف"}).json()["id"]
        assert user_a.delete(f"/api/v1/projects/{project_id}").status_code == 204
        assert user_a.get(f"/api/v1/projects/{project_id}").status_code == 404

    def test_cross_user_access_is_not_found(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        project_id = user_a.post("/api/v1/projects", json={"title": "خاص"}).json()["id"]
        response = user_b.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 404
        assert response.json()["code"] == "resource_not_found"

    def test_cross_user_delete_is_not_found(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        project_id = user_a.post("/api/v1/projects", json={"title": "خاص"}).json()["id"]
        assert user_b.delete(f"/api/v1/projects/{project_id}").status_code == 404
        # Still present for the owner.
        assert user_a.get(f"/api/v1/projects/{project_id}").status_code == 200

    def test_projects_are_scoped_per_user(self, two_users) -> None:  # type: ignore[no-untyped-def]
        user_a, user_b = two_users
        user_a.post("/api/v1/projects", json={"title": "مشروع أ"})
        user_b.post("/api/v1/projects", json={"title": "مشروع ب"})
        assert user_a.get("/api/v1/projects").json()["total"] == 1
        assert user_b.get("/api/v1/projects").json()["total"] == 1

    def test_malformed_uuid_rejected(self, user_a: AuthedClient) -> None:
        response = user_a.get("/api/v1/projects/not-a-uuid")
        assert response.status_code == 422

    def test_random_uuid_is_not_found(self, user_a: AuthedClient) -> None:
        response = user_a.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert response.status_code == 404
