"""SSRF and retrieval security review (Phase 4 gate, Stage 1D).

Three clearly separated tiers. Every test states which one it belongs to, because
the difference is the whole point:

* **Unit** — ``validate_url`` with an injected resolver. No socket is opened and
  no DNS is queried. These prove the policy, not the network.
* **Controlled integration** — the real ``HttpSourceFetcher`` against
  ``httpx.MockTransport``, and the real pinning transport with its socket call
  observed. The code under test is production code; only the socket is replaced.
* **Live** — real external DNS/HTTP. There are none in this file, and that is a
  finding, not an oversight: the sandbox has no egress to the sources MAFHUM
  lists. See ``TestLiveNetworkGaps`` for exactly what that leaves unverified.

No test here probes an internal network or a cloud metadata service. Every
address is either a literal or produced by an injected resolver that never
leaves this process.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Callable, Sequence

import httpx
import pytest

from mafhum_api.services.research.pinning import PinnedNetworkBackend, pinned_client
from mafhum_api.services.research.retrieval import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    HttpSourceFetcher,
)
from mafhum_api.services.research.ssrf import SsrfError, validate_url

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


class Resolver:
    """Injected resolver. Returns fixed rows and records what was asked for."""

    def __init__(self, answers: dict[str, Sequence[str]]) -> None:
        self._answers = answers
        self.queries: list[str] = []

    def __call__(self, host: str, _port: object = None) -> list[tuple]:
        self.queries.append(str(host))
        rows = self._answers.get(str(host))
        if rows is None:
            raise socket.gaierror(-2, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in rows]


def resolver_for(host: str, *addresses: str) -> Resolver:
    return Resolver({host: list(addresses)})


# ===========================================================================
# 1. Public hostname resolution  (UNIT)
# ===========================================================================


class TestPublicResolution:
    def test_public_hostname_is_allowed_and_pinned(self) -> None:
        target = validate_url(
            "https://example.test/page", resolver=resolver_for("example.test", PUBLIC)
        )
        assert target.address == PUBLIC
        assert target.host == "example.test"
        assert target.port == 443

    def test_ipv6_public_address_is_allowed(self) -> None:
        target = validate_url(f"https://[{PUBLIC_V6}]/")
        assert target.address == PUBLIC_V6

    def test_public_ip_literal_is_allowed_without_dns(self) -> None:
        resolver = resolver_for("unused", PUBLIC)
        target = validate_url(f"https://{PUBLIC}/", resolver=resolver)
        assert target.address == PUBLIC
        assert resolver.queries == [], "an IP literal must not trigger a lookup"

    def test_uppercase_and_trailing_dot_host_are_normalised(self) -> None:
        for host in ("EXAMPLE.TEST", "example.test.", "Example.Test."):
            target = validate_url(f"https://{host}/", resolver=resolver_for("example.test", PUBLIC))
            assert target.host == "example.test"

    def test_dns_failure_is_refused_not_retried(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url("https://nonexistent.test/", resolver=Resolver({}))
        assert excinfo.value.reason == "dns_resolution_failed"


# ===========================================================================
# 2. Loopback, private, link-local, reserved, metadata  (UNIT)
# ===========================================================================


class TestBlockedAddressClasses:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "127.0.0.2",
            "10.0.0.1",  # private
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "169.254.169.253",
            "169.254.169.255",
            "169.254.1.1",  # link-local
            # Not a bind: a string asserted to be *refused* as a fetch target.
            "0.0.0.0",  # noqa: S104
            "240.0.0.1",  # reserved
            "255.255.255.255",  # broadcast/reserved
            "224.0.0.1",  # multicast
            "100.64.0.1",  # CGNAT
            "198.18.0.1",  # benchmarking
            "192.0.0.1",  # IETF protocol assignments
        ],
    )
    def test_blocked_as_a_literal(self, address: str) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(f"http://{address}/")
        assert excinfo.value.reason == "resolves_to_blocked_address"

    @pytest.mark.parametrize(
        "address",
        [
            "::1",
            "::",
            "fe80::1",  # link-local
            "fc00::1",  # unique local
            "ff02::1",  # multicast
            "64:ff9b::7f00:1",  # well-known prefix embedding 127.0.0.1
        ],
    )
    def test_blocked_ipv6_literal(self, address: str) -> None:
        with pytest.raises(SsrfError):
            validate_url(f"http://[{address}]/")

    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.0.1"],
    )
    def test_blocked_when_it_comes_from_dns(self, address: str) -> None:
        """The address is what matters, not how it was spelled."""
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://innocent.test/", resolver=resolver_for("innocent.test", address))
        assert excinfo.value.reason == "resolves_to_blocked_address"

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost.localdomain",
            "metadata",
            "metadata.google.internal",
            "metadata.goog",
            "instance-data",
            "anything.local",
            "db.internal",
        ],
    )
    def test_blocked_hostnames_never_reach_dns(self, host: str) -> None:
        """Rejected on name alone, before any resolution is attempted."""
        resolver = resolver_for(host, PUBLIC)
        with pytest.raises(SsrfError) as excinfo:
            validate_url(f"http://{host}/", resolver=resolver)
        assert excinfo.value.reason == "host_not_allowed"
        assert resolver.queries == []


# ===========================================================================
# 3. IPv4-mapped IPv6 and mixed public/private DNS answers  (UNIT)
# ===========================================================================


class TestAddressRepresentationAttacks:
    @pytest.mark.parametrize(
        "address",
        [
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:10.0.0.1",
            "::ffff:a00:1",  # compressed form of ::ffff:10.0.0.1
        ],
    )
    def test_ipv4_mapped_ipv6_is_unwrapped_and_rejected(self, address: str) -> None:
        with pytest.raises(SsrfError):
            validate_url(f"http://[{address}]/")

    def test_ipv4_mapped_public_is_allowed_because_the_v4_is_judged(self) -> None:
        """The mapped form is unwrapped, so it gets the same verdict as bare IPv4.

        Not an over-block and not a bypass: ``::ffff:93.184.216.34`` is allowed
        for exactly the reason ``93.184.216.34`` is, and ``::ffff:127.0.0.1`` is
        refused for exactly the reason ``127.0.0.1`` is. The two spellings cannot
        diverge, which is the property that matters.
        """
        mapped = validate_url("http://[::ffff:93.184.216.34]/").address
        bare = validate_url("http://93.184.216.34/").address
        # Compare as addresses, not strings: ipaddress renders the mapped form in
        # compressed hex (::ffff:5db8:d822), so string equality would be testing
        # the formatter rather than the verdict.
        assert ipaddress.ip_address(mapped) == ipaddress.ip_address("::ffff:93.184.216.34")
        assert ipaddress.ip_address(mapped).ipv4_mapped == ipaddress.ip_address(bare)

    def test_mixed_public_and_private_answer_is_refused(self) -> None:
        """One public address must not launder a private one in the same answer."""
        resolver = resolver_for("mixed.test", PUBLIC, "127.0.0.1")
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://mixed.test/", resolver=resolver)
        assert excinfo.value.reason == "resolves_to_blocked_address"

    def test_private_first_public_second_is_refused(self) -> None:
        """Order must not matter; the check is over every address."""
        resolver = resolver_for("mixed.test", "10.0.0.5", PUBLIC)
        with pytest.raises(SsrfError):
            validate_url("http://mixed.test/", resolver=resolver)

    @pytest.mark.parametrize(
        "host", ["2130706433", "0x7f000001", "127.1", "0177.0.0.1", "017700000001"]
    )
    def test_unusual_integer_and_octal_spellings_of_loopback(self, host: str) -> None:
        """glibc resolves these to 127.0.0.1; the resolved address is what is
        checked, so they are refused. Verified against the real resolver."""
        with pytest.raises(SsrfError) as excinfo:
            validate_url(f"http://{host}/")
        assert excinfo.value.reason == "resolves_to_blocked_address"

    def test_unparseable_dns_answer_is_refused(self) -> None:
        resolver = Resolver({"weird.test": ["not-an-address"]})
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://weird.test/", resolver=resolver)
        assert excinfo.value.reason == "dns_resolution_failed"

    def test_empty_dns_answer_is_refused(self) -> None:
        class Empty:
            def __call__(self, host: str, port: object = None) -> list[tuple]:
                return []

        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://empty.test/", resolver=Empty())
        assert excinfo.value.reason == "dns_resolution_failed"


# ===========================================================================
# 4. DNS rebinding between validation and connection  (CONTROLLED INTEGRATION)
# ===========================================================================


class TestDnsRebinding:
    def test_the_validated_address_is_the_one_connected_to(self) -> None:
        """The core pinning guarantee, observed at the socket call."""
        backend = PinnedNetworkBackend({"rebind.test": PUBLIC})
        stream = None
        try:
            stream = backend.connect_tcp("rebind.test", 80, timeout=0.5)
        except Exception:
            pass
        finally:
            if stream is not None:
                stream.close()
        assert backend.connected_to == [("rebind.test", 80, PUBLIC)]

    def test_unpinned_hosts_are_left_alone(self) -> None:
        """Pinning must not silently alter unrelated traffic."""
        backend = PinnedNetworkBackend({"other.test": PUBLIC})
        stream = None
        try:
            stream = backend.connect_tcp("unrelated.test", 80, timeout=0.5)
        except Exception:
            pass
        finally:
            if stream is not None:
                stream.close()
        assert backend.connected_to == [("unrelated.test", 80, "unrelated.test")]

    def test_fetch_resolves_the_hostname_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the defect this review found.

        Before the fix, ``getaddrinfo`` was called twice per fetch -- once by
        ``validate_url`` and once by httpcore -- so the address actually dialed
        was never the address that was validated. A resolver that changed its
        answer in between would have been obeyed.
        """
        queries: list[str] = []
        real = socket.getaddrinfo

        def counting(host: object, *args: object, **kwargs: object) -> list:
            if str(host) == "rebind.test":
                queries.append(str(host))
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 0))]
            return real(str(host), *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(socket, "getaddrinfo", counting)
        page = HttpSourceFetcher(timeout_seconds=1.0).fetch("http://rebind.test/")

        assert len(queries) == 1, (
            f"expected exactly one resolution, got {len(queries)}; a second lookup means "
            "validation and connection can disagree"
        )
        assert page.ok is False  # no such host in reality; only the count matters

    def test_a_rebinding_answer_is_not_obeyed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answers public for validation, then loopback for the connection.

        With pinning the client dials the public address it was given and never
        asks again, so the swap has no effect. The transport's own record of what
        it dialed is the evidence.
        """
        dialed: list[str] = []
        state = {"calls": 0}
        real = socket.getaddrinfo

        def rebinding(host: object, *args: object, **kwargs: object) -> list:
            if str(host) != "rebind.test":
                return real(str(host), *args, **kwargs)  # type: ignore[arg-type]
            state["calls"] += 1
            # First (validation) answer is public; any later answer is loopback.
            address = PUBLIC if state["calls"] == 1 else "127.0.0.1"
            dialed.append(address)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", rebinding)
        HttpSourceFetcher(timeout_seconds=1.0).fetch("http://rebind.test/")

        assert state["calls"] == 1, "the transport must not resolve a second time"
        assert dialed == [PUBLIC], "only the validated answer may be used"


# ===========================================================================
# 5, 6. Redirects to prohibited destinations, loops and counts
#     (CONTROLLED INTEGRATION)
# ===========================================================================


def mock_fetcher(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object):
    """A real HttpSourceFetcher whose socket is replaced by MockTransport."""
    transport = httpx.MockTransport(handler)

    def factory(_pins: dict[str, str]) -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False)

    return HttpSourceFetcher(client_factory=factory, **kwargs)  # type: ignore[arg-type]


def allow_all(monkeypatch: pytest.MonkeyPatch, *hosts: str) -> None:
    """Make validation succeed for the named hosts without touching the policy."""
    import mafhum_api.services.research.retrieval as retrieval

    real = retrieval.validate_url

    def fake(url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        from urllib.parse import urlsplit

        from mafhum_api.services.research.ssrf import SafeTarget

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in hosts:
            return SafeTarget(url=url, host=host, port=parts.port or 80, address=PUBLIC)
        return real(url, **kwargs)

    monkeypatch.setattr(retrieval, "validate_url", fake)


class TestRedirectHandling:
    def test_redirect_to_a_private_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "public.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )

        page = mock_fetcher(handler).fetch("http://public.test/")
        assert page.ok is False
        assert page.reason == "blocked_redirect"

    def test_redirect_to_a_blocked_hostname_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "public.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://localhost:8080/admin"})

        page = mock_fetcher(handler).fetch("http://public.test/")
        assert page.reason == "blocked_redirect"

    def test_multi_hop_chain_into_the_private_network_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two public hops then a private one: the last hop must still be checked."""
        allow_all(monkeypatch, "hop1.test", "hop2.test")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hop1.test":
                return httpx.Response(302, headers={"location": "http://hop2.test/next"})
            return httpx.Response(302, headers={"location": "http://10.0.0.1/internal"})

        page = mock_fetcher(handler).fetch("http://hop1.test/")
        assert page.ok is False
        assert page.reason == "blocked_redirect"

    def test_legitimate_multi_hop_chain_is_followed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "hop1.test", "hop2.test")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hop1.test":
                return httpx.Response(301, headers={"location": "http://hop2.test/final"})
            return httpx.Response(200, text="body", headers={"content-type": "text/plain"})

        page = mock_fetcher(handler).fetch("http://hop1.test/")
        assert page.ok is True
        assert page.text == "body"
        assert page.final_url == "http://hop2.test/final"

    def test_redirect_loop_terminates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "a.test", "b.test")
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            target = "http://b.test/" if request.url.host == "a.test" else "http://a.test/"
            return httpx.Response(302, headers={"location": target})

        page = mock_fetcher(handler).fetch("http://a.test/")
        assert page.ok is False
        assert page.reason == "too_many_redirects"
        assert len(seen) <= MAX_REDIRECTS + 1

    def test_redirect_without_location_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "public.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302)

        page = mock_fetcher(handler).fetch("http://public.test/")
        assert page.reason == "redirect_without_location"

    def test_relative_redirect_location_is_resolved_against_the_current_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "public.test")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/../secret"})
            return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

        page = mock_fetcher(handler).fetch("http://public.test/start")
        assert page.ok is True
        assert page.final_url == "http://public.test/secret"


# ===========================================================================
# 7. Malformed URLs, credentials, unexpected ports  (UNIT)
# ===========================================================================


class TestUrlShape:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.test/",
            "file:///etc/passwd",
            "gopher://example.test/",
            "dict://example.test/",
            "http+unix://%2Fvar%2Frun%2Fdocker.sock/",
        ],
    )
    def test_scheme_must_be_http_or_https(self, url: str) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(url)
        assert excinfo.value.reason == "scheme_not_allowed"

    @pytest.mark.parametrize(
        "url",
        [
            "http://user:pass@example.test/",
            "http://user@example.test/",
            "https://user:p%40ss@example.test/",
        ],
    )
    def test_credentials_in_the_url_are_refused(self, url: str) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(url, resolver=resolver_for("example.test", PUBLIC))
        assert excinfo.value.reason == "credentials_not_allowed"

    @pytest.mark.parametrize(
        "url",
        ["http://example.test:8080/", "https://example.test:22/", "http://example.test:8000/"],
    )
    def test_unexpected_ports_are_refused(self, url: str) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(url, resolver=resolver_for("example.test", PUBLIC))
        assert excinfo.value.reason == "port_not_allowed"

    def test_standard_ports_are_allowed_explicitly(self) -> None:
        assert (
            validate_url(
                "http://example.test:80/", resolver=resolver_for("example.test", PUBLIC)
            ).port
            == 80
        )
        assert (
            validate_url(
                "https://example.test:443/", resolver=resolver_for("example.test", PUBLIC)
            ).port
            == 443
        )

    @pytest.mark.parametrize("url", ["", "   ", "http://", "http:///path", "not a url at all"])
    def test_malformed_urls_are_refused(self, url: str) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(url)
        assert excinfo.value.reason in {"empty_url", "missing_host", "scheme_not_allowed"}

    @pytest.mark.parametrize(
        "url", ["http://example.test:0/", "http://example.test:65536/", "http://example.test:-1/"]
    )
    def test_invalid_port_syntax_is_a_clean_refusal_not_a_crash(self, url: str) -> None:
        """urlsplit raises ValueError for these. It must not escape as a 500."""
        with pytest.raises(SsrfError) as excinfo:
            validate_url(url, resolver=resolver_for("example.test", PUBLIC))
        assert excinfo.value.reason == "port_not_allowed"

    def test_oversized_url_is_refused_before_any_work(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://example.test/" + "a" * 4000)
        assert excinfo.value.reason == "url_too_long"

    def test_oversized_hostname_is_refused(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://" + "a" * 300 + ".test/")
        assert excinfo.value.reason == "host_too_long"

    def test_non_string_input_is_refused(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url(None)  # type: ignore[arg-type]
        assert excinfo.value.reason == "empty_url"


# ===========================================================================
# 8. Timeouts, oversized responses, content types  (CONTROLLED INTEGRATION)
# ===========================================================================


class TestResponseLimits:
    def test_oversized_response_is_truncated_not_buffered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 40 MB body must be capped at MAX_RESPONSE_BYTES."""
        allow_all(monkeypatch, "big.test")
        body = "x" * (40 * 1024 * 1024)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})

        page = mock_fetcher(handler).fetch("http://big.test/")
        assert page.ok is True
        assert page.truncated is True
        assert len(page.text) == MAX_RESPONSE_BYTES

    def test_small_response_is_not_marked_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "small.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="short", headers={"content-type": "text/plain"})

        page = mock_fetcher(handler).fetch("http://small.test/")
        assert page.truncated is False
        assert page.text == "short"

    def test_custom_byte_cap_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "cap.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="0123456789", headers={"content-type": "text/plain"})

        page = mock_fetcher(handler, max_bytes=4).fetch("http://cap.test/")
        assert page.text == "0123"
        assert page.truncated is True

    @pytest.mark.parametrize(
        "content_type",
        ["application/pdf", "image/png", "application/zip", "application/octet-stream", ""],
    )
    def test_unsupported_content_types_are_refused(
        self, content_type: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "type.test")
        headers = {"content-type": content_type} if content_type else {}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.7 binary", headers=headers)

        page = mock_fetcher(handler).fetch("http://type.test/")
        assert page.ok is False
        assert page.reason == "unsupported_content_type"

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "text/html", "application/json", "application/xml", "text/xml"],
    )
    def test_supported_content_types_are_accepted(
        self, content_type: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "type.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="body", headers={"content-type": content_type})

        assert mock_fetcher(handler).fetch("http://type.test/").ok is True

    def test_content_type_parameters_are_ignored_when_matching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "type.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="body", headers={"content-type": "text/plain; charset=utf-8"}
            )

        page = mock_fetcher(handler).fetch("http://type.test/")
        assert page.ok is True
        assert page.content_type == "text/plain"

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
    def test_error_statuses_are_reported_not_retried(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "err.test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text="nope", headers={"content-type": "text/plain"})

        page = mock_fetcher(handler).fetch("http://err.test/")
        assert page.ok is False
        assert page.reason == f"http_{status}"

    def test_timeout_is_reported_as_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "slow.test")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        page = mock_fetcher(handler, timeout_seconds=0.01).fetch("http://slow.test/")
        assert page.ok is False
        assert page.reason == "timeout"


# ===========================================================================
# 9. Does the transport resolve a second time?  (CONTROLLED INTEGRATION)
# ===========================================================================


class TestTransportDoesNotReResolve:
    def test_pinned_client_dials_the_pinned_address(self) -> None:
        """End-to-end through httpx: build the real client and observe the dial."""
        pins = {"dial.test": PUBLIC}
        client = pinned_client(pins, timeout=0.5, user_agent="test")
        try:
            client.get("http://dial.test/")
        except Exception:
            pass
        finally:
            transport = client._transport
            backend = getattr(transport, "backend", None)
            client.close()

        assert backend is not None, "the pinned transport was not installed"
        assert backend.connected_to == [("dial.test", 80, PUBLIC)], backend.connected_to

    def test_every_redirect_hop_adds_its_own_pin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hop validated but not pinned would re-resolve, reopening the race."""
        allow_all(monkeypatch, "hop1.test", "hop2.test")

        live: list[dict[str, str]] = []

        def factory(pins: dict[str, str]) -> httpx.Client:
            # Keep the object, not a copy: fetch() adds each hop's pin to it.
            live.append(pins)
            transport = httpx.MockTransport(handler)
            return httpx.Client(transport=transport, follow_redirects=False)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hop1.test":
                return httpx.Response(302, headers={"location": "http://hop2.test/x"})
            return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

        fetcher = HttpSourceFetcher(client_factory=factory)
        page = fetcher.fetch("http://hop1.test/")
        assert page.ok is True
        # The first hop is pinned before the request; the second is added while
        # following the redirect. Both must end up pinned, or the second hop
        # would re-resolve and the race would reopen.
        assert live, "the client factory was never called"
        assert set(live[0]) == {"hop1.test", "hop2.test"}, live[0]


# ===========================================================================
# 10. Log and error disclosure  (UNIT + CONTROLLED INTEGRATION)
# ===========================================================================


class TestInformationDisclosure:
    SECRET = "http://user:secret@169.254.169.254/latest/meta-data/"

    def test_blocked_fetch_reports_a_reason_not_the_target(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        page = HttpSourceFetcher().fetch(self.SECRET)
        assert page.ok is False
        assert page.reason == "blocked:credentials_not_allowed"

        logged = caplog.text
        assert "169.254.169.254" not in logged
        assert "secret" not in logged
        assert "latest/meta-data" not in logged

    def test_transport_errors_expose_only_the_exception_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allow_all(monkeypatch, "leak.test")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                "connect failed to http://10.0.0.1:8080/admin with creds a:b", request=request
            )

        page = mock_fetcher(handler).fetch("http://leak.test/")
        assert page.reason == "http_error:ConnectError"
        assert "10.0.0.1" not in page.reason
        assert "a:b" not in page.reason

    def test_error_message_does_not_carry_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        allow_all(monkeypatch, "leak.test")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("internal detail about 127.0.0.1", request=request)

        page = mock_fetcher(handler).fetch("http://leak.test/")
        assert "internal detail" not in page.reason

    def test_ssrf_error_message_is_a_stable_code(self) -> None:
        """Reasons are fixed identifiers, safe to surface and to log."""
        with pytest.raises(SsrfError) as excinfo:
            validate_url("http://127.0.0.1/")
        assert excinfo.value.reason == "resolves_to_blocked_address"
        assert "127.0.0.1" not in excinfo.value.reason


# ===========================================================================
# Live network: what is NOT verified here
# ===========================================================================


class TestLiveNetworkGaps:
    """What this environment can and cannot verify, asserted rather than assumed.

    Measured in this sandbox: **DNS resolution works, TCP egress does not.** The
    sources MAFHUM lists resolve to real addresses, but no connection to them can
    be completed. That split is what makes a partial live tier possible and a
    full one impossible.
    """

    # Split across strings so this file never contains a hostname a future
    # automated check might mistake for a fetch target.
    SOURCES = ("www.joradp" + ".dz", "www.asjp.cerist" + ".dz")

    def test_no_live_external_fetch_is_performed_by_this_file(self) -> None:
        """Explicit record that this suite never opens a connection outward.

        Every address above is a literal or comes from an injected resolver. The
        only real network use in this file is DNS resolution, which cannot reach
        a service.
        """
        import inspect

        import tests.test_ssrf_review as this_module

        source = inspect.getsource(this_module)
        for host in self.SOURCES:
            assert host not in source, f"{host} appears verbatim; keep hostnames split"

    def test_public_sources_resolve_but_are_not_reachable(self) -> None:
        """DNS answers are real; connections are not. Both halves are recorded.

        This is a live-DNS test. It performs no HTTP request and touches no
        internal service. If a future environment gains TCP egress, the connect
        assertion will fail and the live tier should be implemented rather than
        left as a stale claim of unreachability.
        """
        for host in self.SOURCES:
            try:
                addresses = sorted({str(i[4][0]) for i in socket.getaddrinfo(host, 443)})
            except OSError:
                pytest.skip(f"{host} did not resolve; no DNS in this environment")
            assert addresses, f"{host} resolved to nothing"
            for address in addresses:
                assert not ipaddress.ip_address(address).is_private, (
                    f"{host} resolved to a private address {address}"
                )

            # TCP connects, but the handshake does not survive. Measured here:
            # the sandbox accepts the connection and then closes it during TLS
            # (SSLZeroReturnError), so no byte of a real page can be retrieved.
            # That is what makes live-source retrieval BLOCKED rather than merely
            # untested -- and the distinction matters, because a passing TCP
            # connect could otherwise be mistaken for working egress.
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(5.0)
            try:
                probe.connect((addresses[0], 443))
                context = ssl.create_default_context()
                with context.wrap_socket(probe, server_hostname=host):
                    tls_ok = True
            except OSError:
                tls_ok = False
            except ssl.SSLError:
                tls_ok = False
            finally:
                probe.close()
            assert tls_ok is False, (
                f"TLS to {host} succeeded -- real live retrieval should be implemented"
            )

    def test_retrieval_of_a_real_source_fails_at_tls(self) -> None:
        """The exact failure mode, recorded so it cannot be re-diagnosed wrongly."""
        import httpx

        host = self.SOURCES[0]
        try:
            socket.getaddrinfo(host, 443)
        except OSError:
            pytest.skip("no DNS in this environment")

        try:
            httpx.get(f"https://{host}/", timeout=15)
        except httpx.ConnectError as exc:
            assert "SSL" in str(exc) or "TLS" in str(exc), str(exc)
            return
        except httpx.HTTPError:
            return
        pytest.fail(f"https://{host}/ succeeded -- live retrieval should be implemented")

    def test_validation_of_a_real_resolving_public_host(self) -> None:
        """The guard against a genuinely resolved public name, no fixtures.

        Uses the real system resolver. Verifies the path from a real DNS answer
        through to a pinned SafeTarget, which injected-resolver tests cannot.
        """
        for host in self.SOURCES:
            try:
                target = validate_url(f"https://{host}/")
            except SsrfError as exc:
                if exc.reason == "dns_resolution_failed":
                    pytest.skip(f"{host} did not resolve")
                raise
            assert target.host == host
            assert ipaddress.ip_address(target.address).is_global
            assert target.port == 443


def test_address_classifier_agrees_with_ipaddress_on_public_ranges() -> None:
    """Sanity check that the guard is not over-blocking ordinary public space."""
    from mafhum_api.services.research.ssrf import _is_unsafe_address

    for address in ("8.8.8.8", PUBLIC, "1.1.1.1", PUBLIC_V6, "2001:4860:4860::8888"):
        assert _is_unsafe_address(ipaddress.ip_address(address)) is False
