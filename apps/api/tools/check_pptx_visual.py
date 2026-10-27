"""Pre-render structural audit for a generated MAFHUM deck.

This is NOT a pixel renderer. No LibreOffice or PowerPoint is available in the
verification environment, so nobody can claim visual fidelity here. What this
tool does is catch the defects that most often make an editable PPTX look wrong
when a human finally opens it:

* bidi control characters or reversed strings (the classic "fix" that breaks
  mixed Arabic/French citations),
* missing paragraph direction / complex-script font / language tags,
* empty or missing chart and table objects,
* text boxes whose declared size cannot plausibly hold their text,
* a slide size that is not the intended 16:9,
* a broken ZIP/OOXML package.

It emits a JSON report and exits non-zero on a hard failure. Warnings (possible
overflow) are reported but do not fail the gate, because estimating text height
without a font engine is approximate.

Usage
-----
    python apps/api/tools/check_pptx_visual.py deck.pptx --rtl
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

BIDI_CONTROLS = re.compile(r"[\u202a-\u202e\u2066-\u2069\u200e\u200f]")
EXPECTED_WIDTH_IN = 13.333
EXPECTED_HEIGHT_IN = 7.5
EMU_PER_INCH = 914400


def _zip_checks(path: Path) -> dict[str, Any]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            problems.append(f"corrupt member: {bad}")
        names = set(archive.namelist())
        slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
        charts = sorted(n for n in names if re.match(r"ppt/charts/chart\d+\.xml$", n))
        joined = ""
        for name in slides:
            joined += archive.read(name).decode("utf-8", errors="replace")
        return {
            "slide_parts": len(slides),
            "chart_parts": len(charts),
            "rtl_paragraphs": len(re.findall(r'<a:pPr[^>]*rtl="1"', joined)),
            "table_parts": joined.count("<a:tbl>"),
            "complex_script_runs": joined.count("<a:cs"),
            "bidi_controls": len(BIDI_CONTROLS.findall(joined)),
            "language_tags": len(re.findall(r'lang="(?:ar-DZ|fr-FR)"', joined)),
            "problems": problems,
            "raw_xml": joined,
        }


def _overflow_warnings(path: Path) -> list[str]:
    from pptx import Presentation  # local import: only needed for shape geometry

    warnings: list[str] = []
    presentation = Presentation(str(path))
    for index, slide in enumerate(presentation.slides, start=1):
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text.strip()
            if not text:
                continue
            width_in = (shape.width or 0) / EMU_PER_INCH
            height_in = (shape.height or 0) / EMU_PER_INCH
            if width_in <= 0 or height_in <= 0:
                warnings.append(f"slide {index}: text box has zero size")
                continue
            # Rough capacity: ~14 chars per inch at 18pt, ~2 lines per 0.5in.
            capacity = max(1, int(width_in * 14 * max(1, height_in * 2)))
            if len(text) > capacity:
                warnings.append(f"slide {index}: ~{len(text)} chars in a box sized for ~{capacity}")
    return warnings


def audit(path: Path, *, require_rtl: bool) -> dict[str, Any]:
    checks = _zip_checks(path)
    raw = checks.pop("raw_xml")
    problems: list[str] = list(checks.pop("problems"))

    if checks["slide_parts"] < 1:
        problems.append("no slide parts")
    if checks["chart_parts"] < 1:
        problems.append("no chart part — the chart slide is not an editable chart")
    if checks["table_parts"] < 1:
        problems.append("no table part — the bibliography is not an editable table")
    if checks["bidi_controls"]:
        problems.append(
            "bidi control characters found (text was manipulated, not stored logically)"
        )
    if require_rtl:
        if checks["rtl_paragraphs"] == 0:
            problems.append("no RTL paragraph markers in an Arabic deck")
        if checks["complex_script_runs"] == 0:
            problems.append("no complex-script typeface; Arabic shaping may fall back badly")
    if checks["language_tags"] == 0:
        problems.append("no Arabic/French language tags on runs")
    if "مَفْهُوم" not in raw and "MAFHUM" not in raw:
        problems.append("product marker missing from slide text")
    if BIDI_CONTROLS.search(raw):
        problems.append("bidi controls in raw XML")

    from pptx import Presentation

    presentation = Presentation(str(path))
    width_in = (presentation.slide_width or 0) / EMU_PER_INCH
    height_in = (presentation.slide_height or 0) / EMU_PER_INCH
    if abs(width_in - EXPECTED_WIDTH_IN) > 0.05 or abs(height_in - EXPECTED_HEIGHT_IN) > 0.05:
        problems.append(f"unexpected slide size {width_in:.2f}x{height_in:.2f}in")

    report = {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "slide_size_in": [round(width_in, 3), round(height_in, 3)],
        "require_rtl": require_rtl,
        **checks,
        "warnings": _overflow_warnings(path),
        "problems": problems,
        "render_check": "NOT PERFORMED: no LibreOffice/PowerPoint in this environment",
    }
    report["ok"] = not problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("deck", type=Path)
    parser.add_argument("--rtl", action="store_true", help="require Arabic direction markers")
    args = parser.parse_args(argv)
    if not args.deck.is_file():
        print("::error::deck file not found", flush=True)
        return 2
    report = audit(args.deck, require_rtl=args.rtl)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not report["ok"]:
        print(f"::error::visual pre-flight failed: {'; '.join(report['problems'])}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
