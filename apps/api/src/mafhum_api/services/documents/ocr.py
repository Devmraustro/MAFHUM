"""Whether this build can read text out of a scanned document.

**It cannot.** Nothing here pretends otherwise: no adapter is registered, so
``status()`` reports ``available=False`` with the reason, and
``GET /documents/limits`` publishes that answer to the UI, which then tells the
student that a scan will not be searchable.

The reason this module exists at all, rather than a ``False`` literal at the call
site, is that there were two claims in the codebase about why OCR was absent —
the endpoint said nothing, and the PDF parser's docstring implied the only obstacle
was the missing ``tesseract`` binary. Both are now answered from one place, and
adding a real backend means implementing ``_BACKEND`` and flipping one constant
that the tests can see.

A concrete backend would need, at minimum:

* an OCR engine binary (``tesseract`` with ``ara`` + ``fra`` traineddata, or a
  Python wheel that bundles its own models),
* page rasterisation (``pypdfium2``, since PyMuPDF is AGPL and excluded by the
  reuse policy — see ``docs/OSS_REUSE_REGISTER.md``),
* a per-page timeout and a memory ceiling, because a 300-page scan is a denial of
  service waiting to happen,
* a provenance marker on the produced text, so a citation can say "OCR" instead of
  implying the author typed it.

Those steps were **not** performed, and no OCR test exists that would pass. The
honest statement is that MAFHUM extracts text layers only.
"""

from __future__ import annotations

from dataclasses import dataclass

# Set to a callable when a backend is implemented. Kept as a module constant so
# ``status()`` and the PDF parser docstring can never disagree with the endpoint.
_BACKEND: object | None = None

_REASON = (
    "OCR is not implemented in this build: PDFs and DOCX files are read from their "
    "text layer only, so a scanned document is reported as scanned and its text is "
    "not searchable."
)


@dataclass(frozen=True, slots=True)
class OcrStatus:
    """What the server can actually do, and why."""

    available: bool
    reason: str


def status() -> OcrStatus:
    """Report real capability, never intent.

    ``available`` is derived from whether a backend object exists, not from a
    configuration flag: a flag would let a deployment advertise OCR while
    producing nothing.
    """
    if _BACKEND is None:
        return OcrStatus(available=False, reason=_REASON)
    # Unreachable until a backend is implemented; deliberately strict so a
    # half-registered backend does not silently advertise itself.
    raise NotImplementedError("an OCR backend was registered without implementing this branch")
