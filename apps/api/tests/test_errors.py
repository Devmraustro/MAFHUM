"""Safe error envelope and request-ID propagation tests."""

from tests.conftest import client


def test_unknown_route_returns_envelope():
    c = client()
    resp = c.get("/v1/does-not-exist")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "http_404"
    assert body["message"]
    assert body["request_id"]
    assert resp.headers.get("x-request-id") == body["request_id"]


def test_validation_error_envelope():
    c = client()
    resp = c.post("/api/v1/ai/generate", json={})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["field_errors"]
    assert any(fe["field"] == "prompt" for fe in body["field_errors"])
    assert resp.headers.get("x-request-id") == body["request_id"]


def test_generate_contract_and_determinism():
    c = client()
    payload = {"prompt": "اذكر خطوات البحث الأكاديمي مختصرًا", "max_tokens": 256}
    r1 = c.post("/api/v1/ai/generate", json=payload)
    r2 = c.post("/api/v1/ai/generate", json=payload)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    b1, b2 = r1.json(), r2.json()
    assert b1["draft"] is True
    assert b1["provider"] == "mock"
    assert b1["model"] == "mock"
    assert b1["text"] == b2["text"], "mock provider must be deterministic"
    assert b1["request_id"] == r1.headers.get("x-request-id")
    # A different prompt must produce different output.
    r3 = c.post("/api/v1/ai/generate", json={"prompt": "totally different prompt text"})
    b3 = r3.json()
    assert b3["text"] != b1["text"]
