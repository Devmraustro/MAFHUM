"""DNS pinning: make the HTTP client connect to the address we validated.

Why this module exists
----------------------
``ssrf.validate_url`` resolves the hostname and returns the address it judged
safe. If the client then resolves the hostname *again* when it connects, the
second answer is the one actually used, and the validation proved nothing about
it. That is the classic DNS rebinding race: answer with a public address while
being validated, then answer with ``169.254.169.254`` when the socket opens.

Measured against the original implementation, ``socket.getaddrinfo`` was called
**twice** per fetch for the same hostname — once by ``validate_url`` and once by
httpcore — while ``SafeTarget.address`` was never read. The pinning the guard
documented was never wired up.

How the pin works
-----------------
A custom httpcore network backend intercepts ``connect_tcp`` and substitutes the
pinned address for the hostname. The URL host is left alone, which matters:
httpcore still passes the original hostname as ``server_hostname`` for TLS, so
SNI and certificate verification continue to work normally. Rewriting the URL to
an IP instead would have broken certificate validation for every HTTPS source.

The mapping is explicit and per-fetch. A hostname that is not pinned resolves
normally, which keeps ordinary clients unaffected.
"""

from __future__ import annotations

import socket
from typing import Any

import httpcore
import httpx

# Hostname -> address, as validated. Keys are lowercased hostnames without port.
PinMap = dict[str, str]


class PinnedNetworkBackend(httpcore.SyncBackend):
    """Connects to the address we validated instead of re-resolving."""

    def __init__(self, pins: PinMap) -> None:
        self.pins = pins
        # Recorded so tests can assert which address was actually dialed.
        self.connected_to: list[tuple[str, int, str]] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        requested = str(host)
        pinned = self.pins.get(requested.lower())
        target = pinned if pinned is not None else requested
        self.connected_to.append((requested, int(port), target))
        return super().connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedTransport(httpx.HTTPTransport):
    """An ``httpx.HTTPTransport`` whose pool uses :class:`PinnedNetworkBackend`."""

    def __init__(self, pins: PinMap, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.backend = PinnedNetworkBackend(pins)
        # Rebuild the pool with our backend. httpx 0.28 constructs its own
        # httpcore.ConnectionPool here; httpx is pinned to 0.28.1 in
        # pyproject.toml, so this stays correct for the supported version.
        self._pool = httpcore.ConnectionPool(network_backend=self.backend, retries=0)


def pinned_client(pins: PinMap, *, timeout: float, user_agent: str) -> httpx.Client:
    """Build an ``httpx.Client`` that connects only to ``pins`` for those hosts.

    Redirects stay disabled: the caller validates every hop itself.
    """
    transport = PinnedTransport(pins)
    return httpx.Client(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        headers={
            "User-Agent": user_agent,
            # Advertise identity only. No credentials, no cookies.
            "Accept": "text/plain, text/html;q=0.9, */*;q=0.5",
            "Accept-Language": "ar, fr;q=0.8",
        },
    )


def is_loopback(address: str) -> bool:
    """Helper for tests that need to know whether an address is loopback."""
    import ipaddress

    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def resolve_all(host: str) -> list[str]:
    """Resolve every address for ``host``. Used by tests and diagnostics."""
    infos = socket.getaddrinfo(host, None)
    return sorted({str(info[4][0]).split("%")[0] for info in infos or []})
