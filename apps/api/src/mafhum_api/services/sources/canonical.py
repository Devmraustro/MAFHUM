"""Conservative URL canonicalization for source deduplication.

Two source records should be treated as the same source only when their URLs
provably denote the same resource. That is a narrower claim than "these URLs look
similar", so this module normalizes only the components whose equivalence is safe
to establish and leaves everything else alone.

Normalized (safe):

* **Fragment** — dropped. A fragment is never sent to the server and is resolved
  entirely by the client, so ``…/paper#page=3`` and ``…/paper`` fetch the same
  bytes.
* **Scheme and host case** — lowercased. Both are case-insensitive per RFC 3986.
* **Trailing dot on the host** — removed; it is the DNS root label, not part of
  the name.
* **Default port** — ``:80`` on http and ``:443`` on https are removed, because
  they denote the same endpoint as omitting them.

Deliberately **not** normalized, because equivalence cannot be established:

* **http vs https** — different transports, potentially different servers.
* **``www.`` prefix** — stripping it is a common source of false merges; plenty of
  hosts serve different content with and without it.
* **Path case** — paths are case-sensitive on most servers.
* **Query string** — kept verbatim. Reordering or dropping parameters can change
  what a search or repository URL resolves to, and some are signed.
* **Trailing slash** (other than on an empty path) — ``/a`` and ``/a/`` are
  distinct resources on many servers.

Anything that cannot be parsed, or whose scheme is not http/https, returns
``None``: an unparseable URL is not a duplicate of anything.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048


def canonicalize_url(url: str | None) -> str | None:
    """Return a canonical form of ``url``, or ``None`` if it cannot be canonicalized.

    ``None`` means "no identity can be established", which callers must treat as
    "not a duplicate" rather than as a value to compare.
    """
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        return None

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        return None

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None

    port = parts.port
    netloc = host
    if port is not None and port != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{port}"
    if parts.username or parts.password:
        # Credentials in a URL make it unsafe to treat as an identity.
        return None

    path = parts.path or "/"
    # Keep the query verbatim: parameter order and presence can be meaningful.
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def urls_are_equivalent(a: str | None, b: str | None) -> bool:
    """True only when both URLs canonicalize to the same non-empty identity."""
    left = canonicalize_url(a)
    right = canonicalize_url(b)
    return left is not None and left == right
