"""Tests for the parsers and the processing pipeline, using real fixture files.

Nothing here mocks a parser: the assertions run pypdf and python-docx against
genuinely generated PDFs and DOCX files containing synthetic Arabic and French.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mafhum_api.services.documents.parsers.base import ParseError
from mafhum_api.services.documents.parsers.docx_parser import DocxParser
from mafhum_api.services.documents.parsers.pdf_parser import PdfParser
from mafhum_api.services.documents.pipeline import (
    CODE_CORRUPT,
    CODE_EMPTY_EXTRACTION_SCANNED,
    CODE_TOO_MANY_PAGES,
    PipelineError,
    PipelineLimits,
    ProcessingPipeline,
)
from tests.fixtures_content import (
    ARABIC_DIACRITIC_TEXT,
    ARABIC_PARAGRAPHS,
    ARABIC_TITLE,
    FRENCH_PARAGRAPHS,
    FRENCH_TITLE,
    make_corrupt_docx,
    make_corrupt_pdf,
    make_docx,
    make_docx_with_macro_part,
    make_pdf,
    make_scanned_like_pdf,
    make_table_docx,
)


@pytest.fixture
def limits() -> PipelineLimits:
    return PipelineLimits(max_pages=20, max_chars=200_000, max_segment_chars=1500)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestPdfParser:
    def test_extracts_arabic_text_in_logical_order(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=2))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert ARABIC_TITLE in joined
        assert ARABIC_PARAGRAPHS[0] in joined

    def test_page_count_and_numbers(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=3))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.page_count == 3
        assert [page.number for page in result.pages] == [1, 2, 3]

    def test_extracts_french_text(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "fr.pdf", make_pdf("french", pages=1))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert FRENCH_TITLE in joined
        assert FRENCH_PARAGRAPHS[0] in joined

    def test_preserves_arabic_diacritics(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=1))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        # Harakat must survive extraction untouched.
        assert "بِسْمِ" in joined
        assert "\u0650" in joined and "\u0652" in joined

    def test_records_parser_identity(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=1))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.parser_name == "pypdf"
        assert result.parser_version

    def test_text_pdf_not_flagged_as_scanned(self, tmp_path: Path, limits) -> None:  # type: ignore[no-untyped-def]
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=1))
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.is_scanned is False

    def test_scanned_pdf_detected_and_not_fabricated(self, tmp_path: Path) -> None:
        path = write(tmp_path, "scan.pdf", make_scanned_like_pdf())
        result = PdfParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.is_scanned is True
        assert result.char_count == 0
        with pytest.raises(ParseError) as info:
            from mafhum_api.services.documents.parsers.base import require_text

            require_text(result)
        assert info.value.code == "empty_extraction_scanned"

    def test_corrupt_pdf_raises_typed_error(self, tmp_path: Path) -> None:
        path = write(tmp_path, "bad.pdf", make_corrupt_pdf())
        with pytest.raises(ParseError) as info:
            PdfParser().parse(path, max_pages=20, max_chars=200_000)
        assert info.value.code in {"corrupt_pdf", "unreadable_pdf", "no_pages"}

    def test_page_limit_enforced(self, tmp_path: Path) -> None:
        path = write(tmp_path, "many.pdf", make_pdf("french", pages=3))
        with pytest.raises(ParseError) as info:
            PdfParser().parse(path, max_pages=2, max_chars=200_000)
        assert info.value.code == "too_many_pages"

    def test_char_budget_enforced(self, tmp_path: Path) -> None:
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=2))
        with pytest.raises(ParseError) as info:
            PdfParser().parse(path, max_pages=20, max_chars=50)
        assert info.value.code == "extracted_text_too_large"

    def test_supported_detection(self, tmp_path: Path) -> None:
        parser = PdfParser()
        assert parser.supported(write(tmp_path, "a.pdf", make_pdf("french", pages=1)))
        assert not parser.supported(write(tmp_path, "b.pdf", b"not a pdf at all"))


class TestDocxParser:
    def test_extracts_arabic_paragraphs(self, tmp_path: Path) -> None:
        path = write(tmp_path, "ar.docx", make_docx("arabic"))
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert ARABIC_TITLE in joined
        for paragraph in ARABIC_PARAGRAPHS:
            assert paragraph in joined

    def test_extracts_french_paragraphs(self, tmp_path: Path) -> None:
        path = write(tmp_path, "fr.docx", make_docx("french"))
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert FRENCH_TITLE in joined
        assert FRENCH_PARAGRAPHS[0] in joined

    def test_preserves_arabic_diacritics(self, tmp_path: Path) -> None:
        path = write(tmp_path, "ar.docx", make_docx("arabic"))
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert ARABIC_DIACRITIC_TEXT in joined

    def test_explicit_page_breaks_produce_logical_pages(self, tmp_path: Path) -> None:
        path = write(tmp_path, "br.docx", make_docx("arabic", page_breaks=2))
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.page_count >= 2
        assert result.metadata["page_semantics"] == "logical_breaks_not_render_pagination"

    def test_tables_are_extracted(self, tmp_path: Path) -> None:
        path = write(tmp_path, "tbl.docx", make_table_docx())
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert "O(n log n)" in joined
        assert "O(n²)" in joined

    def test_macro_part_reported_but_never_read(self, tmp_path: Path) -> None:
        path = write(tmp_path, "macro.docx", make_docx_with_macro_part())
        result = DocxParser().parse(path, max_pages=20, max_chars=200_000)
        assert result.metadata.get("macro_part_present") == "true"
        # The parser still returns only body text; no macro bytes leak in.
        joined = " ".join(p for _, p in result.all_paragraphs())
        assert "not-a-real-macro-container" not in joined

    def test_corrupt_docx_raises_typed_error(self, tmp_path: Path) -> None:
        path = write(tmp_path, "bad.docx", make_corrupt_docx())
        with pytest.raises(ParseError) as info:
            DocxParser().parse(path, max_pages=20, max_chars=200_000)
        assert info.value.code == "corrupt_docx"

    def test_supported_detection(self, tmp_path: Path) -> None:
        parser = DocxParser()
        assert parser.supported(write(tmp_path, "a.docx", make_docx("arabic")))
        assert not parser.supported(write(tmp_path, "b.docx", b"PK\x03\x04garbage"))


class TestPipeline:
    def test_arabic_pdf_end_to_end(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=2))
        outcome = ProcessingPipeline().run(path, "pdf", limits)
        assert outcome.segments
        assert outcome.language_hint == "ar"
        assert outcome.page_count == 2
        assert outcome.char_count > 0
        assert outcome.parser_name == "pypdf"
        assert any(s.page_number == 2 for s in outcome.segments)

    def test_french_docx_end_to_end(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "fr.docx", make_docx("french"))
        outcome = ProcessingPipeline().run(path, "docx", limits)
        assert outcome.language_hint == "fr"
        assert outcome.parser_name == "python-docx"

    def test_segment_sizes_respected(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=2))
        outcome = ProcessingPipeline().run(path, "pdf", limits)
        assert all(len(s.text) <= limits.max_segment_chars * 2 for s in outcome.segments)

    def test_ordinals_are_contiguous(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "ar.pdf", make_pdf("arabic", pages=2))
        outcome = ProcessingPipeline().run(path, "pdf", limits)
        assert [s.ordinal for s in outcome.segments] == list(range(len(outcome.segments)))

    def test_scanned_pdf_reports_scanned_failure(
        self, tmp_path: Path, limits: PipelineLimits
    ) -> None:
        path = write(tmp_path, "scan.pdf", make_scanned_like_pdf())
        with pytest.raises(PipelineError) as info:
            ProcessingPipeline().run(path, "pdf", limits)
        assert info.value.code == CODE_EMPTY_EXTRACTION_SCANNED
        assert "OCR" in info.value.message

    def test_corrupt_pdf_maps_to_safe_code(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "bad.pdf", make_corrupt_pdf())
        with pytest.raises(PipelineError) as info:
            ProcessingPipeline().run(path, "pdf", limits)
        assert info.value.code == CODE_CORRUPT

    def test_page_limit_maps_to_safe_code(self, tmp_path: Path) -> None:
        path = write(tmp_path, "many.pdf", make_pdf("french", pages=3))
        tight = PipelineLimits(max_pages=2, max_chars=200_000, max_segment_chars=1500)
        with pytest.raises(PipelineError) as info:
            ProcessingPipeline().run(path, "pdf", tight)
        assert info.value.code == CODE_TOO_MANY_PAGES

    def test_format_mismatch_is_rejected(self, tmp_path: Path, limits: PipelineLimits) -> None:
        # A DOCX handed to the pipeline as "pdf" must not be parsed as a DOCX.
        path = write(tmp_path, "wrong.docx", make_docx("arabic"))
        with pytest.raises(PipelineError) as info:
            ProcessingPipeline().run(path, "pdf", limits)
        assert info.value.code == CODE_CORRUPT

    def test_unknown_format_rejected(self, tmp_path: Path, limits: PipelineLimits) -> None:
        path = write(tmp_path, "x.pdf", make_pdf("french", pages=1))
        with pytest.raises(PipelineError):
            ProcessingPipeline().run(path, "txt", limits)

    def test_failure_message_is_arabic_and_safe(
        self, tmp_path: Path, limits: PipelineLimits
    ) -> None:
        path = write(tmp_path, "bad.pdf", make_corrupt_pdf())
        with pytest.raises(PipelineError) as info:
            ProcessingPipeline().run(path, "pdf", limits)
        assert info.value.message
        assert "pypdf" not in info.value.message.lower()
        assert "/" not in info.value.message
