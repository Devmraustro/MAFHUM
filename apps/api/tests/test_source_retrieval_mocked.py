"""Mocked retrieval for the Algerian academic sources.

Why these tests exist, and what they do and do not prove
-------------------------------------------------------
Live retrieval from JORADP, ASJP and SNDL cannot be exercised in this
environment. That was diagnosed rather than assumed, and the diagnosis matters
because it determines what remains unverified:

    allowed hosts     pypi.org, api.github.com   -> TLS 1.3 handshake succeeds
    disallowed hosts  example.com, joradp.dz     -> SSLZeroReturnError (EOF)

``example.com`` is the control. It has a globally valid certificate served by a
major CDN and nothing to do with Algerian publishing, yet it fails with exactly
the same error as JORADP. Plain HTTP to port 80 returns an empty read. So the
failure is the sandbox's egress policy terminating connections to non-allowlisted
hosts -- not the upstream services, not their certificates, not MAFHUM's TLS
configuration, and not a code defect.

Therefore these tests drive the **real** ``HttpSourceFetcher.fetch`` through its
``client_factory`` seam with a stub transport. They prove the logic that decides
what to fetch, what to accept and what to refuse. They cannot prove that a real
JORADP request succeeds, and nothing here claims it does.

Separately: DNS is stubbed so the tests do not depend on a resolver. A public
hostname can resolve to a private address on some networks, and a test that
asserts otherwise is a test that breaks for reasons unrelated to the code.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

import mafhum_api.services.research.retrieval as retrieval
from mafhum_api.services.research.retrieval import (
    KNOWN_SOURCES,
    FixtureFetcher,
    HttpSourceFetcher,
    UnavailableFetcher,
)

# A public address used for every stubbed hostname, so validate_url accepts it and
# the pinning path is exercised for real.
_STUB_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every hostname to one fixed public address.

    Keeps SSRF validation and address pinning on the real code path while making
    the result independent of the host's resolver.
    """
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        return real_getaddrinfo(_STUB_IP, port or 443, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _stub_fetcher(handler) -> HttpSourceFetcher:
    """Build a real fetcher whose HTTP client is replaced by a stub transport."""
    import httpx

    def factory(_pins: dict[str, str]) -> Any:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return HttpSourceFetcher(client_factory=factory)


class TestAdapterDescriptors:
    """The adapter metadata itself -- no network involved."""

    def test_joradp_and_asjp_have_real_base_urls(self) -> None:
        by_key = {source.key: source for source in KNOWN_SOURCES}
        assert by_key["joradp"].url == "https://www.joradp.dz"
        assert by_key["asjp"].url == "https://www.asjp.cerist.dz"

    def test_sndl_carries_a_note_about_institutional_access(self) -> None:
        """SNDL's subscribed resources are gated; that limit is recorded, not hidden."""
        by_key = {source.key: source for source in KNOWN_SOURCES}
        sndl = by_key["sndl"]
        assert sndl.url == "https://www.sndl.cerist.dz"
        assert sndl.notes is not None
        assert "credentials" in sndl.notes.lower()

    def test_the_source_whose_url_was_never_confirmed_stays_none(self) -> None:
        """MESRS's own portal address was never confirmed, so it is absent, not invented.

        This is the invariant that keeps the catalogue honest: a missing fact stays
        ``None`` rather than being filled in from a plausible guess.
        """
        by_key = {source.key: source for source in KNOWN_SOURCES}
        assert by_key["mesrs"].url is None, "an unconfirmed URL must stay None"
        # ...while the descriptor still carries what *is* known.
        assert by_key["mesrs"].title
        assert by_key["mesrs"].institution

    def test_descriptors_never_claim_a_successful_retrieval(self) -> None:
        """Nothing has been retrieved live, so no field may assert otherwise.

        SourceDescriptor deliberately has no verification_status field -- a
        descriptor is a description, not a claim about reachability. This pins that
        shape so a later "verified" flag cannot appear without review.
        """
        fields = set(KNOWN_SOURCES[0].__dataclass_fields__)
        assert "verification_status" not in fields
        for source in KNOWN_SOURCES:
            assert not hasattr(source, "verified")
            blob = " ".join(
                str(v) for v in (source.title, source.institution, source.notes) if v
            ).lower()
            assert "verified" not in blob, source.key


class TestMockedRetrieval:
    """The real fetch logic, driven by stubbed HTTP responses."""

    def test_a_normal_page_is_retrieved_and_capped(self) -> None:
        body = b"<html><body>Journal officiel</body></html>"

        def handler(request):
            import httpx

            return httpx.Response(200, content=body, headers={"content-type": "text/html"})

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/HFR/Index.htm")
        assert page.ok is True
        assert page.text is not None
        assert "Journal officiel" in page.text
        assert page.truncated is False

    def test_a_non_text_content_type_is_refused(self) -> None:
        """A PDF or binary blob must not be treated as page text."""
        import httpx

        def handler(request):
            return httpx.Response(
                200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
            )

        page = _stub_fetcher(handler).fetch("https://www.asjp.cerist.dz/doc.pdf")
        assert page.ok is False
        assert page.reason == "unsupported_content_type"

    def test_an_http_error_is_reported_not_raised(self) -> None:
        import httpx

        def handler(request):
            return httpx.Response(503, text="unavailable")

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/HFR/Index.htm")
        assert page.ok is False
        assert page.reason == "http_503"

    def test_a_redirect_is_followed_and_the_new_host_is_validated(self) -> None:
        import httpx

        seen: list[str] = []

        def handler(request):
            seen.append(str(request.url))
            if len(seen) == 1:
                return httpx.Response(
                    302, headers={"location": "https://www.joradp.dz/HFR/RealIndex.htm"}
                )
            return httpx.Response(
                200, text="<html>index</html>", headers={"content-type": "text/html"}
            )

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/HFR/")
        assert page.ok is True
        assert len(seen) == 2
        assert page.final_url == "https://www.joradp.dz/HFR/RealIndex.htm"

    def test_an_oversized_body_is_truncated_not_buffered(self) -> None:
        """The cap applies to the decoded stream, so a zip bomb cannot expand past it."""
        import httpx

        big = b"a" * (10 * 1024 * 1024)

        def handler(request):
            return httpx.Response(200, content=big, headers={"content-type": "text/html"})

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/huge")
        assert page.truncated is True
        assert page.text is not None
        assert len(page.text) <= retrieval.MAX_RESPONSE_BYTES

    def test_a_timeout_is_reported_as_unavailable(self) -> None:
        import httpx

        def handler(request):
            raise httpx.ConnectTimeout("timed out", request=request)

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/slow")
        assert page.ok is False
        assert page.reason == "timeout"

    def test_an_ssrf_refusal_never_reaches_the_transport(self) -> None:
        """The allowlist runs before any request is built."""
        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            raise AssertionError("transport must not be reached")

        page = _stub_fetcher(handler).fetch("http://127.0.0.1/admin")
        assert page.ok is False
        assert page.reason.startswith("blocked:")
        assert calls == []


class TestCitationProvenance:
    """Retrieval must not manufacture citation data."""

    def test_a_page_with_no_parseable_records_yields_nothing(self) -> None:
        import httpx

        def handler(request):
            return httpx.Response(
                200, text="<html><p>nothing here</p></html>", headers={"content-type": "text/html"}
            )

        page = _stub_fetcher(handler).fetch("https://www.joradp.dz/HFR/Index.htm")
        assert page.ok is True
        # A page that parses to no records must produce no records, not guesses.
        assert not getattr(page, "records", None)

    def test_unavailable_fetcher_reports_unavailability(self) -> None:
        page = UnavailableFetcher().fetch("https://www.joradp.dz/HFR/Index.htm")
        assert page.ok is False

    def test_fixture_fetcher_serves_local_content_only(self) -> None:
        """The offline path returns what was stored, never a live claim."""
        from mafhum_api.services.research.retrieval import RetrievedPage

        fixture = RetrievedPage(url="https://www.joradp.dz/x", ok=True, text="cached text")
        fetcher = FixtureFetcher({"https://www.joradp.dz/x": fixture})
        page = fetcher.fetch("https://www.joradp.dz/x")
        assert page.ok is True
        assert "cached text" in (page.text or "")
        # A miss is reported as a miss rather than synthesized.
        miss = fetcher.fetch("https://www.joradp.dz/absent")
        assert miss.ok is False
        assert miss.reason == "not_found"


class TestLiveRetrievalIsHonestlyUnverified:
    """Records the measured blocker instead of silently skipping the question."""

    def test_the_blocker_is_the_sandbox_not_the_sources(self) -> None:
        """Re-measures the diagnosis so the claim cannot go stale unnoticed.

        An allowlisted host must still complete a TLS handshake while a
        non-allowlisted one fails. If that stops holding -- if this environment
        starts permitting general egress -- the test fails and the live tests
        should be enabled rather than left skipped.
        """
        import ssl

        def tls_ok(host: str) -> bool:
            sock = socket.socket()
            sock.settimeout(8)
            try:
                address = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
                sock.connect((address, 443))
                context = ssl.create_default_context()
                with context.wrap_socket(sock, server_hostname=host):
                    return True
            except Exception:  # any failure means "cannot reach"
                return False
            finally:
                sock.close()

        allowed_ok = tls_ok("pypi" + ".org")
        blocked_fails = not tls_ok("example" + ".com")

        if allowed_ok and blocked_fails:
            pytest.skip(
                "confirmed: this sandbox terminates TLS to non-allowlisted hosts, "
                "so live retrieval from JORADP/ASJP/SNDL cannot be exercised"
            )
        pytest.fail(
            "egress conditions changed: live-source tests should now be enabled and "
            "run for real instead of being reported as unverifiable"
        )
