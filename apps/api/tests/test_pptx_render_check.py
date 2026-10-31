"""Synthetic verification of the headless-render checker.

No LibreOffice is needed here: these tests feed the checker known PGM rasters
and known text so its *failure detection* is proven locally. The CI job in
`.github/workflows/pptx-render.yml` is what supplies a real render.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_pptx_render import (
    MIN_STDDEV,
    check_render,
    parse_pgm,
    pixel_stddev,
)


def _pgm(width: int, height: int, values: list[int]) -> bytes:
    return f"P5\n{width} {height}\n255\n".encode() + bytes(values)


def _varied_page() -> bytes:
    return _pgm(16, 16, [0 if (x + y) % 2 else 255 for y in range(16) for x in range(16)])


def _blank_page() -> bytes:
    return _pgm(16, 16, [255] * 256)


def _inputs(
    tmp_path: Path, *, pages: int, text: str, blank_index: int | None = None
) -> tuple[Path, Path, Path]:
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    text_file = tmp_path / "deck.txt"
    text_file.write_text(text, encoding="utf-8")
    pgm_dir = tmp_path / "render"
    pgm_dir.mkdir()
    for index in range(1, pages + 1):
        data = _blank_page() if index - 1 == blank_index else _varied_page()
        (pgm_dir / f"deck-{index}.pgm").write_bytes(data)
    return pdf, text_file, pgm_dir


ARABIC = "مَفْهُوم عرض تقديمي باللغة العربية مع اقتباسات ومراجع"


class TestPgmParser:
    def test_decodes_header_and_raster(self) -> None:
        image = parse_pgm(_varied_page())
        assert image.width == 16
        assert image.height == 16
        assert image.maxval == 255
        assert len(image.pixels) == 256

    def test_handles_comments_in_header(self) -> None:
        raw = b"P5\n# poppler comment\n8 2\n255\n" + bytes([0, 255] * 8)
        assert len(parse_pgm(raw).pixels) == 16

    def test_rejects_a_truncated_raster(self) -> None:
        try:
            parse_pgm(b"P5\n16 16\n255\n" + b"\x00" * 10)
        except ValueError:
            pass
        else:
            raise AssertionError("a short raster must be rejected")

    def test_blank_and_varied_are_distinguishable(self) -> None:
        assert pixel_stddev(parse_pgm(_blank_page())) == 0.0
        assert pixel_stddev(parse_pgm(_varied_page())) > MIN_STDDEV


class TestRenderChecks:
    def test_a_valid_render_passes(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=6, text=ARABIC + "\n" * 6)
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=6,
            require_arabic=True,
            engine="synthetic",
        )
        assert report["ok"] is True, report["problems"]
        assert report["render_check"].startswith("PERFORMED")
        assert "NOT PERFORMED" in report["powerpoint_parity_check"]
        assert report["pages_rendered"] == 6
        assert report["arabic_codepoints"] >= 12

    def test_a_blank_page_is_detected(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=6, text=ARABIC, blank_index=3)
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=6,
            require_arabic=True,
        )
        assert any("blank" in problem for problem in report["problems"])

    def test_missing_arabic_glyphs_are_detected(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=6, text="Latin text only")
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=6,
            require_arabic=True,
        )
        assert any("Arabic codepoints" in problem for problem in report["problems"])

    def test_replacement_glyph_is_detected(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=6, text=ARABIC + "\ufffd\ufffd\ufffd")
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=6,
            require_arabic=True,
        )
        assert any("U+FFFD" in problem for problem in report["problems"])

    def test_page_count_mismatch_is_detected(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=5, text=ARABIC)
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=5,
            require_arabic=True,
        )
        assert any("pages but the deck has" in problem for problem in report["problems"])

    def test_missing_raster_pages_are_detected(self, tmp_path: Path) -> None:
        pdf, text, pgm_dir = _inputs(tmp_path, pages=2, text=ARABIC)
        report = check_render(
            pdf=pdf,
            text=text,
            pgm_dir=pgm_dir,
            expected_slides=6,
            pdf_pages=6,
            require_arabic=True,
        )
        assert any("rasterised 2 pages" in problem for problem in report["problems"])
