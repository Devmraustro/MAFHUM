"""Processing pipeline: parse -> normalize -> segment.

This module is deliberately side-effect free: it reads a file from a path and
returns a structured outcome. Persistence happens in ``DocumentService`` inside a
single transaction, which is what lets a failed run leave no partial state.

Extracted text is *data*. It is never evaluated, templated, executed, or fed to
any component that would treat it as an instruction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from mafhum_api.services.documents.normalizer import detect_language_hint
from mafhum_api.services.documents.parsers.base import (
    ParseError,
    ParseResult,
    require_text,
)
from mafhum_api.services.documents.parsers.docx_parser import DocxParser
from mafhum_api.services.documents.parsers.pdf_parser import PdfParser
from mafhum_api.services.documents.segmenter import Segment, segment_paragraphs

logger = logging.getLogger(__name__)

# Safe, non-sensitive failure codes persisted on the document/job. The parser's
# own exception text is never stored or returned.
CODE_CORRUPT = "corrupt_file"
CODE_ENCRYPTED = "encrypted_file"
CODE_TOO_MANY_PAGES = "too_many_pages"
CODE_TEXT_TOO_LARGE = "extracted_text_too_large"
CODE_EMPTY_EXTRACTION = "empty_extraction"
CODE_EMPTY_EXTRACTION_SCANNED = "empty_extraction_scanned"
CODE_UNSUPPORTED = "unsupported_format"
CODE_PARSER_ERROR = "parser_error"

ARABIC_ERROR_MESSAGES: dict[str, str] = {
    CODE_CORRUPT: "تعذّرت قراءة الملف لأنه تالف.",
    CODE_ENCRYPTED: "الملف محمي بكلمة مرور ولا يمكن معالجته.",
    CODE_TOO_MANY_PAGES: "عدد صفحات المستند يتجاوز الحد المسموح به.",
    CODE_TEXT_TOO_LARGE: "النص المستخرج أكبر من الحد المسموح به.",
    CODE_EMPTY_EXTRACTION: "لم يُستخرج أي نص من المستند.",
    CODE_EMPTY_EXTRACTION_SCANNED: ("يبدو أن الملف نسخة مصوّرة بدون نص. خدمة OCR غير متاحة حاليًا."),
    CODE_UNSUPPORTED: "صيغة الملف غير مدعومة.",
    CODE_PARSER_ERROR: "حدث خطأ أثناء تحليل الملف.",
}


class PipelineError(Exception):
    """A processing failure with a safe code and Arabic-safe message."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = ARABIC_ERROR_MESSAGES.get(code, ARABIC_ERROR_MESSAGES[CODE_PARSER_ERROR])
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PipelineLimits:
    max_pages: int
    max_chars: int
    max_segment_chars: int


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    segments: tuple[Segment, ...]
    page_count: int
    char_count: int
    language_hint: str | None
    is_scanned: bool
    parser_name: str
    parser_version: str
    parser_metadata: dict[str, str]


class ProcessingPipeline:
    """Runs the configured parser for a format and produces segments."""

    def __init__(self) -> None:
        self._parsers = {
            PdfParser.format_name: PdfParser(),
            DocxParser.format_name: DocxParser(),
        }

    def parser_for(self, document_format: str):  # type: ignore[no-untyped-def]
        parser = self._parsers.get(document_format)
        if parser is None:
            raise PipelineError(CODE_UNSUPPORTED)
        return parser

    def run(self, path: Path, document_format: str, limits: PipelineLimits) -> PipelineOutcome:
        parser = self.parser_for(document_format)

        # Defence in depth: re-confirm the parser can actually handle this file
        # rather than trusting the format recorded at upload time.
        if not parser.supported(path):
            raise PipelineError(CODE_CORRUPT)

        try:
            result: ParseResult = parser.parse(
                path, max_pages=limits.max_pages, max_chars=limits.max_chars
            )
        except ParseError as exc:
            raise PipelineError(_map_parse_error(exc.code)) from exc
        except (MemoryError, RecursionError) as exc:
            logger.warning("parser resource limit hit", extra={"event": "parser_resource_limit"})
            raise PipelineError(CODE_PARSER_ERROR) from exc
        except Exception as exc:
            # Never let a parser's own message reach the database or a client.
            logger.exception("unexpected parser failure", extra={"event": "parser_failure"})
            raise PipelineError(CODE_PARSER_ERROR) from exc

        try:
            require_text(result)
        except ParseError as exc:
            raise PipelineError(_map_parse_error(exc.code)) from exc

        paragraphs = result.all_paragraphs()
        segments = segment_paragraphs(paragraphs, max_chars=limits.max_segment_chars)
        if not segments:
            raise PipelineError(CODE_EMPTY_EXTRACTION)

        total_chars = sum(segment.char_count for segment in segments)
        if total_chars > limits.max_chars:
            raise PipelineError(CODE_TEXT_TOO_LARGE)

        sample = " ".join(segment.text[:200] for segment in segments[:5])
        return PipelineOutcome(
            segments=tuple(segments),
            page_count=result.page_count,
            char_count=total_chars,
            language_hint=detect_language_hint(sample),
            is_scanned=result.is_scanned,
            parser_name=result.parser_name,
            parser_version=result.parser_version,
            parser_metadata=result.metadata,
        )


def _map_parse_error(code: str) -> str:
    mapping = {
        "corrupt_pdf": CODE_CORRUPT,
        "unreadable_pdf": CODE_CORRUPT,
        "corrupt_docx": CODE_CORRUPT,
        "encrypted_pdf": CODE_ENCRYPTED,
        "too_many_pages": CODE_TOO_MANY_PAGES,
        "too_many_paragraphs": CODE_TEXT_TOO_LARGE,
        "extracted_text_too_large": CODE_TEXT_TOO_LARGE,
        "empty_extraction": CODE_EMPTY_EXTRACTION,
        "empty_extraction_scanned": CODE_EMPTY_EXTRACTION_SCANNED,
        "no_pages": CODE_EMPTY_EXTRACTION,
        "no_content": CODE_EMPTY_EXTRACTION,
    }
    return mapping.get(code, CODE_PARSER_ERROR)
