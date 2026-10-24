"""Parser adapter interface.

Parsers return *structured* output (pages -> paragraphs) rather than one flat
string, so segmentation can preserve page provenance without re-deriving it.
Extracted text is untrusted data: it is never executed, evaluated, or treated as
an instruction to the system.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from mafhum_api.core.errors import FileRejectedError


class ParseError(Exception):
    """Parser-level failure. ``code`` is safe to persist; ``detail`` is not."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page of extracted text. ``number`` is 1-based when known."""

    number: int | None
    paragraphs: tuple[str, ...]

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)


@dataclass(frozen=True, slots=True)
class ParseResult:
    pages: tuple[ParsedPage, ...]
    parser_name: str
    parser_version: str
    is_scanned: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def char_count(self) -> int:
        return sum(page.char_count for page in self.pages)

    def all_paragraphs(self) -> list[tuple[int | None, str]]:
        """Flatten to ``(page_number, paragraph)`` pairs, dropping empties."""
        out: list[tuple[int | None, str]] = []
        for page in self.pages:
            for paragraph in page.paragraphs:
                if paragraph.strip():
                    out.append((page.number, paragraph))
        return out


@runtime_checkable
class DocumentParser(Protocol):
    format_name: str
    parser_name: str
    parser_version: str

    def parse(self, path: Path, *, max_pages: int, max_chars: int) -> ParseResult: ...

    def supported(self, path: Path) -> bool: ...


def require_text(result: ParseResult) -> None:
    """Fail explicitly on empty extraction instead of reporting a blank success."""
    if result.char_count == 0:
        code = "empty_extraction_scanned" if result.is_scanned else "empty_extraction"
        raise ParseError(code)


def guard_char_budget(result: ParseResult, max_chars: int) -> None:
    if result.char_count > max_chars:
        raise FileRejectedError(
            "النص المستخرج يتجاوز الحد المسموح به.",
            details={"reason": "file_too_large", "hint": "extracted_text"},
        )


def merge_wrapped_lines(paragraphs: Sequence[str]) -> list[str]:
    """Join hard-wrapped lines back into paragraphs, conservatively.

    Only lines that clearly continue a sentence are joined (previous line does
    not end with terminal punctuation and is not short/list-like). Arabic and
    French punctuation are both honoured. Diacritics are never stripped here.
    """
    terminal = ".!?:;؟؛…"
    markers = ("-", "•", "·", "*", "◦", "·", "‣", "▪")
    out: list[str] = []
    for raw in paragraphs:
        line = raw.strip()
        if not line:
            continue
        if out and _continues(out[-1], line, terminal, markers):
            out[-1] = f"{out[-1]} {line}".strip()
        else:
            out.append(line)
    return out


def _continues(previous: str, current: str, terminal: str, markers: tuple[str, ...]) -> bool:
    if not previous or previous[-1] in terminal:
        return False
    if previous.startswith(markers) or current.startswith(markers):
        return False
    # A numbered/bulleted line starts a new paragraph.
    stripped = current.lstrip()
    if stripped[:2].rstrip().isdigit() or stripped[:1] in {")", ".", "،"}:
        return False
    # A short previous fragment is usually a heading; do not swallow it. PDF
    # body text lines are typically much longer than a title.
    return len(previous) >= 40
