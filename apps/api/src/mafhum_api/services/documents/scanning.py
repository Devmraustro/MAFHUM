"""Malware scanning: the interface, and an honest default that scans nothing.

**MAFHUM ships no malware scanning.** No engine, no signature database and no
scanner binary is present in the default install, and none could be installed or
exercised in the environment where this was written. This module exists so that
the absence is *stated by the code* rather than implied by a gap, and so that the
integration point is already shaped when an engine is provisioned.

Two rules follow from that and are enforced by the tests:

1. Nothing may report a document as clean unless an engine actually scanned it.
   :class:`NullScanner` can only ever return
   :attr:`ScanVerdict.NOT_SCANNED` — it has no way to produce ``CLEAN``.
2. Structural validation is **not** scanning. The magic-byte, extension,
   archive-shape, decompressed-size and page-count checks in ``validators.py``
   reject malformed and abusive files; they detect no malicious content and must
   never be described as antivirus.

Integration plan (see ``docs/SECURITY_ASSESSMENTS.md``): talk to a ``clamd``
daemon over its socket protocol. Speaking to ``clamd`` across a socket does not
link ``libclamav``, so ClamAV's GPL-2.0 does not reach MAFHUM; linking
``libclamav`` in-process would. A scanner that cannot be reached must return
:attr:`ScanVerdict.ERROR`, and callers that require scanning must treat ``ERROR``
and ``NOT_SCANNED`` as "do not process" (fail closed) rather than as "no threat
found".
"""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable


class ScanVerdict(str, enum.Enum):
    """What a scanner concluded about one upload.

    ``NOT_SCANNED`` and ``ERROR`` are deliberately distinct from ``CLEAN``: a
    caller that requires scanning must reject both, because neither is evidence
    that the bytes are safe.
    """

    NOT_SCANNED = "not_scanned"
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


@runtime_checkable
class MalwareScanner(Protocol):
    """A content scanner for uploaded bytes.

    Implementations must never return ``CLEAN`` without having actually examined
    the content with a real engine and signature set.
    """

    @property
    def available(self) -> bool:
        """Whether a real engine is reachable and able to scan right now."""

    def scan(self, data: bytes, filename: str) -> ScanVerdict:
        """Return a verdict for ``data``. Must not raise for unreachable engines."""


class NullScanner:
    """The shipped default: no engine, so nothing is scanned.

    ``available`` is always ``False`` and ``scan`` always returns
    ``NOT_SCANNED``. There is intentionally no code path that yields ``CLEAN``,
    so an unscanned upload can never be represented as malware-free.
    """

    @property
    def available(self) -> bool:
        return False

    def scan(self, data: bytes, filename: str) -> ScanVerdict:
        return ScanVerdict.NOT_SCANNED


def requires_clean(verdict: ScanVerdict) -> bool:
    """Fail-closed predicate for processing that must not run on unscanned bytes.

    Only an affirmative ``CLEAN`` passes. ``NOT_SCANNED`` and ``ERROR`` both
    mean "no engine vouched for this", which is not the same as "safe".
    """
    return verdict is ScanVerdict.CLEAN


def build_scanner(settings: object) -> MalwareScanner:
    """Return the scanner the configured settings call for.

    The single place that decides whether uploads are scanned. With no ``clamd``
    address configured this returns :class:`NullScanner`, whose ``available`` is
    ``False`` and which can only ever answer ``NOT_SCANNED`` -- so the disclosed
    capability and the actual behaviour come from one object and cannot drift
    apart.

    ``settings`` is typed loosely to avoid importing the config module here, which
    would make this leaf module depend on application configuration.
    """
    from mafhum_api.services.documents.clamd import ClamdScanner

    unix_path = getattr(settings, "clamd_unix_path", None)
    host = getattr(settings, "clamd_host", None)
    if not unix_path and not host:
        return NullScanner()
    return ClamdScanner(
        unix_path=unix_path,
        host=host,
        port=int(getattr(settings, "clamd_port", 3310)),
        timeout_seconds=float(getattr(settings, "clamd_timeout_seconds", 10.0)),
    )
