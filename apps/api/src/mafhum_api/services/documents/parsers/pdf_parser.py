"""PDF text extraction via pypdf.

Why pypdf (see ``docs/OSS_REUSE_REGISTER.md``):
* Permissive BSD-style license (verified in the upstream LICENSE file).
* Pure Python: no native build step, no GPU, no model weights to download.
* Actively maintained (multiple releases per week at the time of evaluation).

PyMuPDF was rejected because its PyPI metadata declares
"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License", and MAFHUM's
reuse policy does not admit AGPL components without an explicit compatibility
review. OCRmyPDF was rejected because it requires the external ``tesseract``
binary, which is not part of the default install.

**No OCR is performed.** A PDF that carries no extractable text layer is
reported as scanned rather than silently returning empty text.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pypdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from mafhum_api.services.documents.parsers.base import (
    ParsedPage,
    ParseError,
    ParseResult,
    merge_wrapped_lines,
)

logger = logging.getLogger(__name__)

# Heuristic: a page with no extractable characters but embedded images is
# almost certainly a scan. Used only to *label* output, never to guess text.
_MIN_PAGE_CHARS_FOR_TEXT = 1


class PdfParser:
    format_name = "pdf"
    parser_name = "pypdf"
    parser_version = pypdf.__version__

    def supported(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(5) == b"%PDF-"
        except OSError:
            return False

    def parse(self, path: Path, *, max_pages: int, max_chars: int) -> ParseResult:
        try:
            reader = PdfReader(str(path), strict=False)
        except PdfReadError as exc:
            raise ParseError("corrupt_pdf") from exc
        except OSError as exc:
            raise ParseError("unreadable_pdf") from exc

        if getattr(reader, "is_encrypted", False):
            # Do not attempt any password; encrypted uploads are out of scope.
            raise ParseError("encrypted_pdf")

        total = len(reader.pages)
        if total > max_pages:
            raise ParseError("too_many_pages")

        pages: list[ParsedPage] = []
        chars = 0
        pages_with_images = 0
        pages_with_text = 0

        for index in range(total):
            try:
                page = reader.pages[index]
                raw = page.extract_text() or ""
            except (PdfReadError, RecursionError, ValueError, KeyError) as exc:
                # A single broken page must not fail the whole document.
                logger.info(
                    "skipping unparsable PDF page",
                    extra={"event": "pdf_page_skipped", "page": index + 1},
                )
                raw = ""
                _ = exc
            paragraphs = tuple(merge_wrapped_lines(raw.splitlines()))
            page_chars = sum(len(p) for p in paragraphs)
            if page_chars >= _MIN_PAGE_CHARS_FOR_TEXT:
                pages_with_text += 1
            if _page_has_images(page):
                pages_with_images += 1
            chars += page_chars
            if chars > max_chars:
                raise ParseError("extracted_text_too_large")
            pages.append(ParsedPage(number=index + 1, paragraphs=paragraphs))

        if not pages:
            raise ParseError("no_pages")

        # Scanned when no page yielded text but at least one carries an image.
        is_scanned = pages_with_text == 0 and pages_with_images > 0
        metadata = {
            "producer": _safe_metadata(reader, "/Producer"),
            "creator": _safe_metadata(reader, "/Creator"),
        }
        return ParseResult(
            pages=tuple(pages),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            is_scanned=is_scanned,
            metadata={k: v for k, v in metadata.items() if v},
        )


def _page_has_images(page: object) -> bool:
    """Best-effort image detection; failures are non-fatal."""
    try:
        resources = getattr(page, "get", lambda *_: None)("/Resources")
        if resources is None:
            return False
        xobject = resources.get("/XObject") if hasattr(resources, "get") else None
        if not xobject:
            return False
        resolved = xobject.get_object() if hasattr(xobject, "get_object") else xobject
        for item in resolved.values():
            obj = item.get_object() if hasattr(item, "get_object") else item
            subtype = obj.get("/Subtype") if hasattr(obj, "get") else None
            if subtype == "/Image":
                return True
        return False
    except Exception:
        return False


def _safe_metadata(reader: PdfReader, key: str) -> str:
    try:
        info = reader.metadata
        if not info:
            return ""
        value = info.get(key)
        return str(value)[:200] if value else ""
    except Exception:
        return ""
