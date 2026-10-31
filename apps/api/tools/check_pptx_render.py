"""Verify a REAL headless render of a generated Arabic PPTX deck.

This is the pixel-level companion to ``check_pptx_visual.py`` (which is
structural only). A CI job converts the deck with LibreOffice Impress, extracts
text with poppler's ``pdftotext`` and rasterises pages with ``pdftoppm -gray``.
This tool then checks, with the Python standard library alone:

* the PDF has exactly as many pages as the PPTX has slides,
* every page rasterises to a non-blank image (pixel standard deviation above a
  threshold), so an empty or all-white slide cannot pass,
* extracted text contains Arabic codepoints and no U+FFFD replacement glyphs
  (the usual symptom of missing Arabic/tofu fonts),
* the render engine and version are recorded in the JSON report.

Scope boundary, stated plainly: this proves **headless LibreOffice** rendering,
not PowerPoint visual parity. Shaping, font substitution and chart/table layout
can still differ in Microsoft PowerPoint on Windows. That check remains a manual
task in the local/Windows handoff.

Usage
-----
    python apps/api/tools/check_pptx_render.py \
        --pptx deck.pptx --pdf deck.pdf --text deck.txt \
        --pgm-dir rendered --require-arabic \
        --engine "LibreOffice 7.6 (headless)"
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARABIC_RANGE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
MIN_STDDEV = 2.0
MIN_ARABIC_CHARS = 12


@dataclass(frozen=True, slots=True)
class PgmImage:
    """A decoded binary P5 PGM (8-bit greyscale)."""

    width: int
    height: int
    maxval: int
    pixels: bytes


def parse_pgm(data: bytes) -> PgmImage:
    """Decode a P5 PGM without Pillow. Raises ValueError on malformed input."""
    offset = 0

    def token() -> bytes:
        nonlocal offset
        while True:
            # Skip whitespace and `#` comments (poppler does not emit these, but
            # the PGM spec allows them and other producers do).
            while offset < len(data) and data[offset : offset + 1].isspace():
                offset += 1
            if offset < len(data) and data[offset : offset + 1] == b"#":
                while offset < len(data) and data[offset : offset + 1] not in (b"\n", b"\r"):
                    offset += 1
                continue
            break
        start = offset
        while offset < len(data) and not data[offset : offset + 1].isspace():
            offset += 1
        if start == offset:
            raise ValueError("truncated PGM header")
        return data[start:offset]

    if token() != b"P5":
        raise ValueError("not a binary P5 PGM")
    width = int(token())
    height = int(token())
    maxval = int(token())
    if width < 1 or height < 1 or maxval not in (15, 255) or width * height > 100_000_000:
        raise ValueError("unsupported PGM dimensions")
    # Exactly one whitespace byte follows maxval before the raster begins.
    offset += 1
    pixels = data[offset : offset + width * height]
    if len(pixels) != width * height:
        raise ValueError("PGM raster is truncated")
    return PgmImage(width=width, height=height, maxval=maxval, pixels=pixels)


def pixel_stddev(image: PgmImage) -> float:
    """Population standard deviation; 0 means a completely uniform page."""
    sample = image.pixels
    if not sample:
        return 0.0
    mean = sum(sample) / len(sample)
    variance = sum((value - mean) ** 2 for value in sample) / len(sample)
    return math.sqrt(variance)


def pdf_page_count(pdf: Path) -> int:
    binary = shutil.which("pdfinfo")
    if not binary:
        raise RuntimeError("pdfinfo is required")
    output = subprocess.run(  # noqa: S603 - fixed argv, no untrusted input
        [binary, str(pdf)], check=True, capture_output=True, text=True
    ).stdout
    match = re.search(r"^Pages:\s+(\d+)\s*$", output, re.MULTILINE)
    if not match:
        raise RuntimeError(f"pdfinfo did not report a page count for {pdf.name}")
    return int(match.group(1))


def pptx_slide_count(pptx: Path) -> int:
    from pptx import Presentation  # local import: only needed at the CLI boundary

    return len(Presentation(str(pptx)).slides)


def check_render(
    *,
    pdf: Path,
    text: Path,
    pgm_dir: Path,
    expected_slides: int,
    pdf_pages: int | None = None,
    require_arabic: bool = False,
    engine: str = "unknown",
    min_stddev: float = MIN_STDDEV,
) -> dict[str, Any]:
    problems: list[str] = []
    pages = pdf_page_count(pdf) if pdf_pages is None else pdf_pages
    if pages != expected_slides:
        problems.append(f"PDF has {pages} pages but the deck has {expected_slides} slides")

    extracted = text.read_text(encoding="utf-8", errors="replace") if text.is_file() else ""
    arabic_chars = len(ARABIC_RANGE.findall(extracted))
    if require_arabic and arabic_chars < MIN_ARABIC_CHARS:
        problems.append(
            f"only {arabic_chars} Arabic codepoints in extracted text (need >= {MIN_ARABIC_CHARS})"
        )
    if "\ufffd" in extracted:
        problems.append("U+FFFD replacement glyph in extracted text — a font is missing")

    images = sorted(pgm_dir.glob("*.pgm"))
    if len(images) != pages:
        problems.append(f"rasterised {len(images)} pages, expected {pages}")
    page_stats: list[dict[str, Any]] = []
    for image_path in images:
        try:
            image = parse_pgm(image_path.read_bytes())
        except ValueError as exc:
            problems.append(f"{image_path.name}: {exc}")
            continue
        deviation = pixel_stddev(image)
        page_stats.append(
            {
                "file": image_path.name,
                "width": image.width,
                "height": image.height,
                "stddev": round(deviation, 3),
            }
        )
        if deviation < min_stddev:
            problems.append(
                f"{image_path.name}: page looks blank (stddev={deviation:.3f} < {min_stddev})"
            )

    return {
        "pdf": str(pdf),
        "engine": engine,
        "render_check": "PERFORMED: headless LibreOffice + poppler rasterisation",
        "powerpoint_parity_check": "NOT PERFORMED: requires Microsoft PowerPoint on Windows",
        "expected_slides": expected_slides,
        "pdf_pages": pages,
        "pages_rendered": len(images),
        "arabic_codepoints": arabic_chars,
        "pages": page_stats,
        "problems": problems,
        "ok": not problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--text", type=Path, required=True)
    parser.add_argument("--pgm-dir", type=Path, required=True)
    parser.add_argument("--require-arabic", action="store_true")
    parser.add_argument("--engine", default="unknown")
    parser.add_argument("--expected-slides", type=int, default=None)
    parser.add_argument("--pdf-pages", type=int, default=None)
    args = parser.parse_args(argv)

    for path in (args.pptx, args.pdf, args.text):
        if not path.is_file():
            print(f"::error::missing required file: {path}", flush=True)
            return 2
    expected = args.expected_slides
    if expected is None:
        expected = pptx_slide_count(args.pptx)
    report = check_render(
        pdf=args.pdf,
        text=args.text,
        pgm_dir=args.pgm_dir,
        expected_slides=expected,
        pdf_pages=args.pdf_pages,
        require_arabic=args.require_arabic,
        engine=args.engine,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not report["ok"]:
        for problem in report["problems"]:
            print(f"::error::{problem}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
