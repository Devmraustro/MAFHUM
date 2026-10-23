"""SSRF defenses for retrieving external sources.

Anything that fetches a user-influenced URL is a request-forgery vector: the
server has network access the user does not, and the interesting targets are
internal. Retrieval is therefore opt-in per URL, and every URL is re-validated
at every hop.

The guard is deliberately pessimistic. It resolves the hostname and rejects the
address *before* any connection is made, then refuses to follow a redirect to a
destination that would not have been allowed directly — otherwise an allowed
public host becomes a one-hop proxy into the private network.

Checks that look redundant are not. Hostnames can resolve to several addresses,
IPv6 has its own reserved ranges, and cloud metadata endpoints are link-local
addresses that a naive "is this private?" test misses.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

# Hostnames that are never acceptable regardless of what they resolve to.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

MAX_URL_LENGTH = 2048
MAX_HOSTNAME_LENGTH = 253


class SsrfError(ValueError):
    """Raised when a URL must not be fetched. The reason is safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SafeTarget:
    """A URL that passed validation, plus the address it resolved to.

    Callers should connect to ``address`` rather than re-resolving, so the
    validated answer is the one actually used. Re-resolving invites a DNS
    rebinding race where the second lookup returns an internal address.
    """

    url: str
    host: str
    port: int
    address: str


# IANA special-purpose ranges that ``ipaddress`` still reports as ``is_global``.
# Deny-by-default cannot catch these on its own, so they are named.
EXTRA_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # 6to4 relay anycast. Deprecated (RFC 7526) and routable to infrastructure.
    ipaddress.ip_network("192.88.99.0/24"),
    # NOTE: ``::ffff:0:0/96`` is deliberately absent. IPv4-mapped addresses are
    # unwrapped at the top of ``_is_unsafe_address`` and judged as the IPv4
    # address they carry, so a mapped loopback is blocked and a mapped public
    # address is allowed -- the same verdict the bare IPv4 would get. Listing the
    # range here would be unreachable code, because the unwrap returns first.
)


def _is_unsafe_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that must not be reached from a user-supplied URL.

    The test is **deny-by-default**: an address is acceptable only if it is
    globally routable *and* not in a special-purpose range. Enumerating the bad
    ranges is not enough -- Python's ``is_private`` omits RFC 6598 shared address
    space (``100.64.0.0/10``), which is carrier infrastructure and exactly the
    kind of target this guard exists to keep out. Asking "is this definitely
    public?" fails closed when the answer is unknown.
    """
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) is unwrapped first, so the embedded
    # IPv4 address is judged on its own merits rather than as an IPv6 literal.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_unsafe_address(ip.ipv4_mapped)

    if not ip.is_global:
        return True

    for network in EXTRA_BLOCKED_NETWORKS:
        if ip.version == network.version and ip in network:
            return True

    # Named explicitly so the intent survives a future change above, and so the
    # cloud metadata endpoint is blocked even if its classification ever shifts.
    explicit = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in ipaddress.ip_network("169.254.169.252/30")
    )
    return explicit


def validate_url(url: str, *, resolver: object | None = None) -> SafeTarget:
    """Validate one URL and return the pinned address to connect to.

    ``resolver`` exists so tests can pin DNS deterministically; it defaults to
    the real ``socket.getaddrinfo``.
    """
    if not isinstance(url, str) or not url.strip():
        raise SsrfError("empty_url")
    if len(url) > MAX_URL_LENGTH:
        raise SsrfError("url_too_long")

    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise SsrfError("malformed_url") from exc

    if parts.scheme not in ALLOWED_SCHEMES:
        raise SsrfError("scheme_not_allowed")
    # file://, gopher:// and friends are already excluded above; this catches
    # credentials smuggled into an otherwise acceptable URL.
    if parts.username or parts.password:
        raise SsrfError("credentials_not_allowed")

    host = parts.hostname
    if not host:
        raise SsrfError("missing_host")
    if len(host) > MAX_HOSTNAME_LENGTH:
        raise SsrfError("host_too_long")

    host = host.rstrip(".").lower()
    if not host:
        raise SsrfError("missing_host")
    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise SsrfError("host_not_allowed")

    try:
        # `parts.port or default` would turn ":0" into 80, silently fetching a
        # different port than the URL asked for.
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        # urlsplit raises for ":0" and out-of-range ports. Without this the
        # caller would see an unhandled ValueError rather than a clean refusal.
        raise SsrfError("port_not_allowed") from exc
    if port not in ALLOWED_PORTS:
        raise SsrfError("port_not_allowed")

    address = _resolve_public_address(host, resolver)
    return SafeTarget(url=url, host=host, port=port, address=address)


def _resolve_public_address(host: str, resolver: object | None) -> str:
    """Resolve ``host`` and return the first address that is safe to reach."""
    # A bare IP literal still goes through the same safety test.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_unsafe_address(literal):
            raise SsrfError("resolves_to_blocked_address")
        return str(literal)

    resolve = resolver if resolver is not None else socket.getaddrinfo
    try:
        infos = resolve(host, None)  # type: ignore[operator]
    except (socket.gaierror, OSError) as exc:
        raise SsrfError("dns_resolution_failed") from exc

    candidates: list[str] = []
    for info in infos or []:
        # getaddrinfo rows are (family, type, proto, canonname, sockaddr).
        sockaddr = info[4]
        raw = sockaddr[0] if isinstance(sockaddr, tuple) else str(sockaddr)
        candidates.append(str(raw).split("%")[0])

    if not candidates:
        raise SsrfError("dns_resolution_failed")

    # Every resolved address must be acceptable: a host that answers with both a
    # public and a private address is not safe to connect to.
    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise SsrfError("dns_resolution_failed") from exc
        if _is_unsafe_address(ip):
            raise SsrfError("resolves_to_blocked_address")

    return candidates[0]
