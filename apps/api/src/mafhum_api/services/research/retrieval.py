"""Retrieval of external sources, with the failure modes made explicit.

Retrieval is **opt-in and off by default**. Core MAFHUM — upload, processing,
grounded answers over a student's own documents — needs no network at all, which
is what keeps the zero-mandatory-paid-service promise intact.

Three fetchers implement one protocol:

* :class:`HttpSourceFetcher` — the real thing. Validates through
  ``ssrf.validate_url``, re-validates every redirect hop, and enforces timeout,
  size and content-type limits.
* :class:`FixtureFetcher` — deterministic, for tests. Never touches a socket.
* :class:`UnavailableFetcher` — the honest default when retrieval is disabled or
  the network is absent. It fails with a clear reason instead of returning
  invented content.

Nothing here fabricates. A source that cannot be reached yields a
``RetrievedPage`` carrying ``ok=False`` and a reason; the caller records that
outcome rather than substituting plausible text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import httpx

from mafhum_api.services.research.pinning import pinned_client
from mafhum_api.services.research.sanitize import neutralize
from mafhum_api.services.research.ssrf import SsrfError, validate_url

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5

# Content types worth parsing as text. Anything else (PDF, images, archives) is
# refused here rather than half-parsed: a binary body decoded as text produces
# garbage that could still be quoted as evidence.
TEXT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "application/xhtml+xml",
        "application/json",
        "application/xml",
        "text/xml",
    }
)


ClientFactory = Callable[[dict[str, str]], "httpx.Client"]


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """A known source. Unknown fields stay ``None``; nothing is inferred."""

    key: str
    title: str
    institution: str | None
    url: str | None
    language: str | None
    notes: str | None = None


@dataclass(slots=True)
class RetrievedPage:
    url: str
    ok: bool
    reason: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    text: str = ""
    retrieved_at: datetime | None = None
    truncated: bool = False


# Canonical URLs below were checked against the publishers' own sites and
# Wikidata. MESRS is listed as an institution with **no URL** because its address
# was not confirmed; a missing URL is honest, an invented one is not.
KNOWN_SOURCES: tuple[SourceDescriptor, ...] = (
    SourceDescriptor(
        key="joradp",
        title="Journal Officiel de la République Algérienne Démocratique et Populaire",
        institution="Secrétariat Général du Gouvernement",
        url="https://www.joradp.dz",
        language="ar,fr",
        notes="Official gazette; only texts published here are legally binding.",
    ),
    SourceDescriptor(
        key="asjp",
        title="Algerian Scientific Journal Platform (ASJP)",
        institution="CERIST",
        url="https://www.asjp.cerist.dz",
        language="ar,fr,en",
    ),
    SourceDescriptor(
        key="sndl",
        title="Système National de Documentation en Ligne (SNDL)",
        institution="CERIST / DGRSDT / MESRS",
        url="https://www.sndl.cerist.dz",
        language="fr",
        notes="Access to subscribed resources requires institutional credentials.",
    ),
    SourceDescriptor(
        key="mesrs",
        title="Ministère de l'Enseignement Supérieur et de la Recherche Scientifique",
        institution="MESRS",
        # Deliberately None: the canonical address was not verified.
        url=None,
        language="ar,fr",
    ),
)


@runtime_checkable
class SourceFetcher(Protocol):
    def fetch(self, url: str) -> RetrievedPage: ...


class UnavailableFetcher:
    """The default when retrieval is not enabled. Fails clearly, invents nothing."""

    def __init__(self, reason: str = "retrieval_disabled") -> None:
        self._reason = reason

    def fetch(self, url: str) -> RetrievedPage:
        return RetrievedPage(url=url, ok=False, reason=self._reason)


class FixtureFetcher:
    """Deterministic fetcher for tests. Never opens a socket."""

    def __init__(self, pages: dict[str, RetrievedPage]) -> None:
        self._pages = pages
        self.requested: list[str] = []

    def fetch(self, url: str) -> RetrievedPage:
        self.requested.append(url)
        page = self._pages.get(url)
        if page is None:
            return RetrievedPage(url=url, ok=False, reason="not_found")
        page.retrieved_at = datetime.now(UTC)
        return page


class HttpSourceFetcher:
    """Fetches over HTTP with SSRF, redirect, size, timeout and type limits."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        user_agent: str = "MAFHUM/0.3 (academic research; respects source terms)",
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        # A seam for tests, so response handling (redirects, size, content type,
        # timeouts) can be exercised against httpx.MockTransport without opening
        # a socket. It replaces the *transport only*: URL validation, per-hop
        # re-validation and the redirect cap still run unchanged. Nothing in the
        # application constructs a fetcher with this argument.
        self._client_factory = client_factory

    def fetch(self, url: str) -> RetrievedPage:
        # Imported lazily so an install without httpx still starts: retrieval is
        # optional and core startup must not depend on it.
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx is a declared dependency
            return RetrievedPage(url=url, ok=False, reason="http_client_unavailable")

        try:
            target = validate_url(url)
        except SsrfError as exc:
            logger.warning(
                "refused to fetch url", extra={"event": "ssrf_blocked", "reason": exc.reason}
            )
            return RetrievedPage(url=url, ok=False, reason=f"blocked:{exc.reason}")

        # Every host we are about to contact is pinned to the address that was
        # validated. Without this the client resolves the name a second time and
        # the answer it gets -- not the one we checked -- is what it connects to.
        pins: dict[str, str] = {target.host: target.address}

        factory = self._client_factory or (
            lambda active: pinned_client(active, timeout=self._timeout, user_agent=self._user_agent)
        )
        try:
            with factory(pins) as client:
                current = target.url
                for _ in range(self._max_redirects + 1):
                    # stream() so the body is capped while it arrives.
                    response = client.send(client.build_request("GET", current), stream=True)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            return RetrievedPage(
                                url=url, ok=False, reason="redirect_without_location"
                            )
                        current = str(httpx.URL(current).join(location))
                        try:
                            # The return value is not optional: it carries the
                            # address this hop must be pinned to.
                            hop = validate_url(current)
                        except SsrfError as exc:
                            logger.warning(
                                "refused redirect target",
                                extra={"event": "ssrf_blocked", "reason": exc.reason},
                            )
                            return RetrievedPage(url=url, ok=False, reason="blocked_redirect")
                        pins[hop.host] = hop.address
                        continue

                    return self._build_page(url, current, response)
                return RetrievedPage(url=url, ok=False, reason="too_many_redirects")
        except httpx.TimeoutException:
            return RetrievedPage(url=url, ok=False, reason="timeout")
        except httpx.HTTPError as exc:
            # Only the exception class name. httpx messages embed the URL and
            # host, and those must not reach a log or an API response.
            return RetrievedPage(url=url, ok=False, reason=f"http_error:{type(exc).__name__}")

    def _build_page(self, original_url: str, final_url: str, response: object) -> RetrievedPage:
        import httpx

        if not isinstance(response, httpx.Response):  # pragma: no cover - typing guard
            return RetrievedPage(url=original_url, ok=False, reason="unexpected_response")
        status = response.status_code
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()

        if status >= 400:
            return RetrievedPage(
                url=original_url,
                ok=False,
                reason=f"http_{status}",
                status_code=status,
                content_type=content_type or None,
                final_url=final_url,
            )
        if content_type not in TEXT_CONTENT_TYPES:
            return RetrievedPage(
                url=original_url,
                ok=False,
                reason="unsupported_content_type",
                status_code=status,
                content_type=content_type or None,
                final_url=final_url,
            )

        # Read the decoded body in chunks and stop at the cap. response.content
        # would buffer the whole thing first, so a small gzip that expands to
        # gigabytes -- or simply a very large page -- would be decompressed and
        # held in memory before any limit could apply. Capping the *decoded*
        # stream bounds both the download and the decompression.
        chunks: list[bytes] = []
        total = 0
        truncated = False
        for chunk in response.iter_bytes():
            remaining = self._max_bytes - total
            if remaining <= 0:
                truncated = True
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total = self._max_bytes
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)

        return RetrievedPage(
            url=original_url,
            ok=True,
            status_code=status,
            content_type=content_type,
            final_url=final_url,
            text=neutralize(b"".join(chunks).decode("utf-8", errors="replace")),
            retrieved_at=datetime.now(UTC),
            truncated=truncated,
        )
