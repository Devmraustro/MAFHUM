"""Deterministic mock provider tests (no credentials, no network, no model)."""

import asyncio

from app.providers.mock import MockProvider


def test_mock_health():
    provider = MockProvider()
    health = asyncio.run(provider.health())
    assert health.status == "ok"
    assert health.provider == "mock"
    assert health.deterministic is True
    assert health.credentials_required is False


def test_mock_generate_is_deterministic():
    provider = MockProvider()

    async def run():
        req1 = type("Req", (), {"prompt": "البحث عن المادة 48 من الدستور", "max_tokens": 128, "temperature": 0.7})()
        req2 = type("Req", (), {"prompt": "البحث عن المادة 48 من الدستور", "max_tokens": 128, "temperature": 0.7})()
        req3 = type("Req", (), {"prompt": "something else", "max_tokens": 128, "temperature": 0.7})()
        r1 = await provider.generate(req1, "req-1")
        r2 = await provider.generate(req2, "req-1")
        r3 = await provider.generate(req3, "req-1")
        return r1, r2, r3

    r1, r2, r3 = asyncio.run(run())
    assert r1.text == r2.text
    assert r1.draft is True
    assert "غير أكاديمي" in r1.text or "Not a source" in r1.text
    assert r1.text != r3.text
    assert r1.request_id == "req-1"


def test_mock_embed_is_deterministic():
    provider = MockProvider()

    async def run():
        req = type("Req", (), {"texts": ["نص عربي", "texte français"]})()
        return await provider.embed(req, "embed-1")

    emb = asyncio.run(run())
    assert len(emb.embeddings) == 2
    assert all(len(v) == 16 for v in emb.embeddings)
    assert all(0.0 <= x <= 1.0 for v in emb.embeddings for x in v)
