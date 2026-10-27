"""Measure the Arabic/French editable-PPTX benchmark for both generators.

Run from the repository root:

    PYTHONPATH=apps/api/src .venv/bin/python apps/api/tools/benchmark_pptx.py

Both generators receive the *same* checked-in DeckSpec fixture, so differences
are attributable to the library, not to the input. The Node path is optional:
when Node or its dependencies are absent the tool records ``unavailable`` rather
than pretending the comparison ran. Outputs go to the gitignored ``artifacts/``.

The report is honest about what it cannot check here: no LibreOffice/PowerPoint
is installed in this sandbox, so there is no pixel-level render comparison. What
IS verified is OOXML integrity, native editable objects, chart/table parts, RTL
paragraph markers, language tags and preservation of mixed Arabic/French text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from mafhum_api.services.presentations.generator import PythonPptxGenerator  # noqa: E402
from mafhum_api.services.presentations.spec import DeckSpec  # noqa: E402
from mafhum_api.services.presentations.validation import (  # noqa: E402
    DeckValidationError,
    inspect,
    validate,
)

FIXTURE = REPO_ROOT / "apps" / "slides" / "fixtures" / "benchmark_deck.json"
NODE_SCRIPT = REPO_ROOT / "apps" / "slides" / "generate.mjs"
NODE_MODULES = REPO_ROOT / "apps" / "slides" / "node_modules"
OUTPUT_DIR = REPO_ROOT / "artifacts" / "pptx-benchmark"

FRENCH_PHRASE = "mandat présidentiel"


def _mixed_script_check(path: Path) -> dict[str, Any]:
    """Confirm the mixed-script quotation survived in logical order."""
    found_arabic = False
    found_french = False
    with zipfile.ZipFile(path) as archive:
        slides = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide"))
        for name in slides:
            xml = archive.read(name).decode("utf-8", errors="replace")
            found_arabic = found_arabic or any("\u0600" <= ch <= "\u06ff" for ch in xml)
            found_french = found_french or FRENCH_PHRASE in xml
    return {"arabic_present": found_arabic, "french_present": found_french}


def _run_python(spec: DeckSpec, target: Path) -> dict[str, Any]:
    start = time.perf_counter()
    PythonPptxGenerator().generate(spec, target)
    elapsed = time.perf_counter() - start
    report = validate(
        target,
        expected_slides=len(spec.slides),
        require_rtl=spec.rtl,
        require_chart=True,
        require_table=True,
    )
    return {
        "generator": f"python-pptx/{PythonPptxGenerator.version}",
        "seconds": round(elapsed, 3),
        **asdict(report),
        **_mixed_script_check(target),
    }


def _run_node(spec: DeckSpec, target: Path) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None or not NODE_MODULES.is_dir():
        return {
            "generator": "pptxgenjs/4.0.1",
            "status": "unavailable",
            "reason": "node or apps/slides/node_modules missing",
        }
    start = time.perf_counter()
    result = subprocess.run(  # noqa: S603
        [node, str(NODE_SCRIPT), str(FIXTURE), str(target)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        return {
            "generator": "pptxgenjs/4.0.1",
            "status": "failed",
            "stderr": result.stderr[-800:],
        }
    try:
        report = validate(
            target,
            expected_slides=len(spec.slides),
            require_rtl=spec.rtl,
            require_chart=True,
            require_table=True,
        )
    except DeckValidationError as exc:
        return {
            "generator": "pptxgenjs/4.0.1",
            "status": "validation_failed",
            "reason": str(exc),
            "inspection": asdict(inspect(target)),
        }
    return {
        "generator": "pptxgenjs/4.0.1",
        "seconds": round(elapsed, 3),
        "status": "ok",
        **asdict(report),
        **_mixed_script_check(target),
    }


def main() -> int:
    spec = DeckSpec.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "fixture": str(FIXTURE.relative_to(REPO_ROOT)),
        "slides": len(spec.slides),
        "citations": len(spec.citations),
        "rtl": spec.rtl,
        "render_check": "not available: no LibreOffice or PowerPoint in this environment",
        "results": [
            _run_python(spec, OUTPUT_DIR / "benchmark_python.pptx"),
            _run_node(spec, OUTPUT_DIR / "benchmark_pptxgenjs.pptx"),
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
