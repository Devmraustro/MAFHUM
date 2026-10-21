"""Health endpoint contract tests."""

import pytest

from tests.conftest import client


def test_live_returns_ok_with_request_id() -> None:
    c = client()
    resp = c.get("/health/live")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "request_id" in body and body["request_id"]
    assert resp.headers.get("x-request-id") == body["request_id"]


def test_ready_degrades_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "get_engine", lambda: None)
    c = client()
    resp = c.get("/health/ready")
    body = resp.json()
    assert body["status"] == "degraded", body
    assert body["checks"]["database"]["status"] == "unavailable"
    assert body["request_id"]
    assert resp.headers.get("x-request-id") == body["request_id"]


def test_versioned_health_aliases() -> None:
    c = client()
    for path in ("/api/v1/health/live", "/api/v1/health/ready"):
        resp = c.get(path)
        assert resp.status_code in (200, 503), (path, resp.text)
        body = resp.json()
        assert "status" in body and "request_id" in body
