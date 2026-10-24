"""DOCX text extraction via python-docx (MIT licensed).

Reading model
-------------
python-docx parses the OOXML parts as XML; it never executes macros, embedded
OLE objects or scripts. We read only the main document part plus table cells.
The presence of a macro part (``word/vbaProject.bin``) is *reported* in
metadata so operators can see it, and the part is never opened.

Page numbers
------------
DOCX is a flow format: real pagination only exists after a renderer lays the
text out. This parser therefore reports a **logical page index** derived from
explicit page/section breaks, and marks it in ``metadata``. When a document has
no explicit break, everything lands on logical page 1. This is documented rather
than presented as true pagination.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

from docx import Document as DocxDocument
from docx.document import Document as _DocumentBody
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from mafhum_api.services.documents.parsers.base import (
    ParsedPage,
    ParseError,
    ParseResult,
)

logger = logging.getLogger(__name__)

import docx as docx_module  # noqa: E402  (import order kept for readability)

_MACRO_PARTS = ("word/vbaProject.bin", "word/vbaData.xml")
_MAX_PARAGRAPHS = 20_000


class DocxParser:
    format_name = "docx"
    parser_name = "python-docx"
    parser_version = docx_module.__version__

    def supported(self, path: Path) -> bool:
        try:
            with zipfile.ZipFile(path) as archive:
                return "word/document.xml" in set(archive.namelist())
        except (zipfile.BadZipFile, OSError):
            return False

    def parse(self, path: Path, *, max_pages: int, max_chars: int) -> ParseResult:
        try:
            document = DocxDocument(str(path))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError, Exception) as exc:
            # python-docx raises package-level exceptions (PackageNotFoundError,
            # BadZipFile) and lxml errors for malformed XML. None of their text
            # is safe to surface, so it is deliberately dropped here.
            raise ParseError("corrupt_docx") from exc

        macro_present = _contains_macro_part(path)

        # ``pages`` accumulates (logical_page_number, [paragraphs]).
        current_paragraphs: list[str] = []
        pages: list[ParsedPage] = []
        chars = 0
        paragraph_count = 0
        page_number = 1

        def flush() -> None:
            nonlocal current_paragraphs
            pages.append(ParsedPage(number=page_number, paragraphs=tuple(current_paragraphs)))
            current_paragraphs = []

        for paragraph_count, block in enumerate(_iter_body_blocks(document), start=1):
            if paragraph_count > _MAX_PARAGRAPHS:
                raise ParseError("too_many_paragraphs")
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    chars += len(text)
                    if chars > max_chars:
                        raise ParseError("extracted_text_too_large")
                    current_paragraphs.append(text)
                if _has_explicit_page_break(block):
                    flush()
                    page_number += 1
                    if page_number > max_pages:
                        raise ParseError("too_many_pages")
            elif isinstance(block, Table):
                for row_text in _iter_table_rows(block, max_chars - chars):
                    chars += len(row_text)
                    if chars > max_chars:
                        raise ParseError("extracted_text_too_large")
                    current_paragraphs.append(row_text)

        flush()
        if not pages:
            raise ParseError("no_content")

        metadata: dict[str, str] = {
            "page_semantics": "logical_breaks_not_render_pagination",
        }
        if macro_present:
            metadata["macro_part_present"] = "true"
        core = document.core_properties
        if core.title:
            metadata["title"] = str(core.title)[:500]
        if core.author:
            metadata["author"] = str(core.author)[:255]
        if core.language:
            metadata["language"] = str(core.language)[:8]

        return ParseResult(
            pages=tuple(pages),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            is_scanned=False,
            metadata=metadata,
        )


def _iter_body_blocks(document: _DocumentBody) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _iter_table_rows(table: Table, remaining_budget: int) -> Iterator[str]:
    """Flatten a table into one tab-separated line per non-empty row."""
    emitted = 0
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        line = "\t".join(cell for cell in cells if cell)
        if not line:
            continue
        if emitted > remaining_budget:
            return
        emitted += len(line)
        yield line


def _has_explicit_page_break(paragraph: Paragraph) -> bool:
    """True for an explicit page break or a page-break-before run."""
    for break_element in paragraph._p.iter(qn("w:br")):
        if break_element.get(qn("w:type")) == "page":
            return True
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is not None:
        page_break_before = properties.find(qn("w:pageBreakBefore"))
        if page_break_before is not None and page_break_before.get(qn("w:val")) not in {
            "false",
            "0",
            "none",
        }:
            return True
    return False


def _contains_macro_part(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return any(part in names for part in _MACRO_PARTS)


def _body(document: _DocumentBody) -> _DocumentBody:
    """Typed accessor retained for callers that need the raw body."""
    return document


__all__ = ["DocxParser", "logger"]
