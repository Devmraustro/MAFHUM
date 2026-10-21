"""Network boundary utilities: trusted proxy IP resolution, HTTPS checks, and Origin validation."""

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

IpNet = ipaddress.IPv4Network | ipaddress.IPv6Network
IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


def _parse_ip(raw: str) -> IpAddr | None:
    try:
        return ipaddress.ip_address(raw.strip())
    except ValueError:
        return None


def _is_trusted(ip: IpAddr, trusted_networks: list[IpNet]) -> bool:
    return any(ip in net for net in trusted_networks)


def resolve_client_ip(request: Request, trusted_networks: list[IpNet]) -> str:
    """Resolve the real client IP without allowing untrusted header spoofing.

    - When the direct socket peer (`request.client.host`) is NOT in
      `trusted_networks`, all `X-Forwarded-For` and `X-Real-IP` headers are
      ignored completely.
    - When the direct socket peer IS an explicitly trusted proxy, `X-Forwarded-For`
      is scanned right-to-left and the rightmost non-trusted valid IP is selected
      (preventing attackers behind a reverse proxy from forging leftmost entries).
    """
    peer_raw = request.client.host if request.client else "unknown"
    peer_ip = _parse_ip(peer_raw)
    if peer_ip is None or not trusted_networks or not _is_trusted(peer_ip, trusted_networks):
        return peer_raw

    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [part.strip() for part in xff.split(",") if part.strip()]
        valid_hops: list[IpAddr] = []
        for hop in reversed(hops):
            parsed = _parse_ip(hop)
            if parsed is None:
                # Malformed hop — abort header parsing rather than trusting forged text.
                break
            valid_hops.append(parsed)
            if not _is_trusted(parsed, trusted_networks):
                return str(parsed)
        if valid_hops:
            return str(valid_hops[-1])

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        parsed_real = _parse_ip(x_real_ip)
        if parsed_real is not None:
            return str(parsed_real)

    return str(peer_ip)


def is_https_request(request: Request, trusted_networks: list[IpNet]) -> bool:
    """Return True if transport is direct TLS or terminated by a trusted proxy."""
    if request.url.scheme.lower() == "https":
        return True
    peer_raw = request.client.host if request.client else ""
    peer_ip = _parse_ip(peer_raw)
    if peer_ip is not None and trusted_networks and _is_trusted(peer_ip, trusted_networks):
        proto = request.headers.get("x-forwarded-proto", "")
        first_proto = proto.split(",")[0].strip().lower()
        return first_proto == "https"
    return False


def _extract_header_origin(raw: str) -> str | None:
    parsed = urlsplit(raw.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def verify_request_origin(
    request: Request,
    allowed_origins: set[str],
    trusted_networks: list[IpNet],
) -> None:
    """Reject cross-origin state-changing requests (defense-in-depth with CSRF)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    origin_header = request.headers.get("origin")
    candidate: str | None = None
    if origin_header:
        if origin_header.strip().lower() == "null":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-origin request forbidden",
            )
        candidate = _extract_header_origin(origin_header)
        if candidate is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Origin header",
            )
    else:
        referer = request.headers.get("referer")
        if referer:
            candidate = _extract_header_origin(referer)
            if candidate is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid Referer header",
                )

    if candidate is None:
        return

    peer_raw = request.client.host if request.client else ""
    peer_ip = _parse_ip(peer_raw)
    forwarded_host = ""
    if peer_ip is not None and trusted_networks and _is_trusted(peer_ip, trusted_networks):
        forwarded_host = (
            request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
        )

    host = forwarded_host or request.headers.get("host", "").strip().lower()
    scheme = "https" if is_https_request(request, trusted_networks) else request.url.scheme.lower()
    self_origin = f"{scheme}://{host}" if host else ""

    if candidate in allowed_origins or (self_origin and candidate == self_origin):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Cross-origin request forbidden",
    )
