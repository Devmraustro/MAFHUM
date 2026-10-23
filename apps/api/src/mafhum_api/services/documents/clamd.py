"""A real malware scanner: the ClamAV ``clamd`` daemon, over its socket protocol.

Why this integration and not a Python binding
---------------------------------------------
``libclamav`` is GPL-2.0, so linking it in-process would put MAFHUM's own code
under GPL terms. Speaking to a ``clamd`` daemon across a socket is a network
conversation, not linkage, so ClamAV's licence does not reach this codebase. That
is also why there is no ClamAV Python package in the dependencies: the protocol
is small enough to implement directly, and every candidate package either pulled
in the GPL binding or was unmaintained.

This module contains **protocol code only**. It brings no engine and no signature
database. Whether malware is actually detected depends entirely on whether an
operator has installed and configured ``clamd`` -- see
``docs/SECURITY_ASSESSMENTS.md`` for what was and was not verified.

Behaviour that matters for safety
---------------------------------
* An unreachable or misbehaving daemon yields :attr:`ScanVerdict.ERROR`, never
  ``CLEAN``. A scanner that cannot answer must not be reported as having found
  nothing.
* ``scan`` does not raise. Callers decide policy; a socket error is not a reason
  to turn an upload into a 500.
* Uploads are streamed to the daemon in bounded chunks, so scanning a large file
  does not require holding a second copy of it in memory.
* Timeouts are enforced on connect and on the whole exchange, so a hung daemon
  cannot stall the request path indefinitely.
"""

from __future__ import annotations

import logging
import socket
from typing import BinaryIO

from mafhum_api.services.documents.scanning import MalwareScanner, ScanVerdict

logger = logging.getLogger(__name__)

# clamd's INSTREAM protocol: a 4-byte big-endian length prefixes each chunk, and
# a zero-length chunk terminates the stream.
_CHUNK_SIZE = 64 * 1024
_TERMINATOR = b"\x00\x00\x00\x00"
# clamd replies with a single NUL-terminated line. Bounded so a daemon that
# misbehaves cannot make us allocate without limit.
_MAX_REPLY = 4096


class ClamdScanner:
    """Scans bytes by asking a ``clamd`` daemon over a Unix socket or TCP.

    Constructed with neither address, ``available`` is ``False`` and ``scan``
    returns ``NOT_SCANNED``: the honest result when no engine is configured.
    """

    def __init__(
        self,
        *,
        unix_path: str | None = None,
        host: str | None = None,
        port: int = 3310,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._unix_path = unix_path
        self._host = host
        self._port = port
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        """Whether an address to talk to was supplied at all."""
        return bool(self._unix_path or self._host)

    @property
    def available(self) -> bool:
        """Whether a daemon actually answers ``PING`` right now.

        A real probe rather than a config check: reporting an engine as available
        when the daemon is down would let unscanned files through a policy that
        trusts that flag.
        """
        if not self.configured:
            return False
        try:
            with self._connect() as sock:
                sock.sendall(b"zPING\0")
                reply = self._read_reply(sock)
        except OSError as exc:
            logger.info(
                "clamd not reachable",
                extra={"event": "clamd_unreachable", "error": type(exc).__name__},
            )
            return False
        return reply.strip("\x00").strip().upper().startswith("PONG")

    def scan(self, data: bytes, filename: str) -> ScanVerdict:
        """Scan in-memory bytes. ``filename`` is used for logging only."""
        if not self.configured:
            return ScanVerdict.NOT_SCANNED
        import io

        return self.scan_stream(io.BytesIO(data), filename=filename, size=len(data))

    def scan_stream(self, stream: BinaryIO, *, filename: str, size: int) -> ScanVerdict:
        """Scan a stream without loading it fully into memory.

        ``size`` is used only for logging; the stream itself is read in bounded
        chunks until it ends.
        """
        if not self.configured:
            return ScanVerdict.NOT_SCANNED
        try:
            with self._connect() as sock:
                sock.sendall(b"zINSTREAM\0")
                sent = 0
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
                    sent += len(chunk)
                sock.sendall(_TERMINATOR)
                reply = self._read_reply(sock)
        except (TimeoutError, OSError) as exc:
            # Never CLEAN: the daemon did not give an answer, so nothing vouched
            # for these bytes.
            logger.warning(
                "clamd scan failed",
                extra={
                    "event": "clamd_scan_failed",
                    "error": type(exc).__name__,
                    "bytes": sent if "sent" in dir() else 0,
                },
            )
            return ScanVerdict.ERROR
        return self._interpret(reply, filename=filename, size=size)

    def _interpret(self, reply: str, *, filename: str, size: int) -> ScanVerdict:
        """Turn a clamd reply line into a verdict.

        clamd answers ``stream: OK``, ``stream: <signature> FOUND`` or
        ``stream: <reason> ERROR``. Anything unrecognised is treated as ``ERROR``
        rather than as a pass, so a protocol change cannot silently turn every
        upload clean.
        """
        body = reply.strip("\x00").strip()
        upper = body.upper()
        if upper.endswith("OK"):
            return ScanVerdict.CLEAN
        if "FOUND" in upper:
            logger.warning(
                "malware detected",
                extra={"event": "malware_found", "reply": body[:200], "bytes": size},
            )
            return ScanVerdict.INFECTED
        logger.warning(
            "clamd returned an unrecognised reply",
            extra={"event": "clamd_unexpected_reply", "reply": body[:200]},
        )
        return ScanVerdict.ERROR

    def _connect(self) -> socket.socket:
        if self._unix_path:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        target: str | tuple[str, int] = (
            self._unix_path if self._unix_path else (self._host or "", self._port)
        )
        try:
            sock.connect(target)
        except OSError:
            sock.close()
            raise
        return sock

    @staticmethod
    def _read_reply(sock: socket.socket) -> str:
        buffer = bytearray()
        while len(buffer) < _MAX_REPLY:
            chunk = sock.recv(256)
            if not chunk:
                break
            buffer.extend(chunk)
            if b"\x00" in chunk:
                break
        return buffer.decode("utf-8", errors="replace")


__all__ = ["ClamdScanner", "MalwareScanner", "ScanVerdict"]
