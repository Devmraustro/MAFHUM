"""Engine-level EICAR verification using MAFHUM's own clamd protocol client.

This is NOT a mock. It talks to a real, running `clamd` with a real downloaded
signature set over TCP, using exactly the client the API uses for uploads. It
must pass before the API-level acceptance runs: otherwise an app-level rejection
could be explained by a probe the engine never saw.

Verified here:
  * a clean control file returns CLEAN;
  * the bare EICAR test string returns INFECTED;
  * the structurally valid EICAR-carrying DOCX that MAFHUM uploads returns
    INFECTED (ClamAV unwraps the ZIP container and sees the stored payload).

Usage:
    python tools/acceptance/engine_eicar_check.py --host 127.0.0.1 --port 3310
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from tools.eicar_probe import EICAR, build_eicar_docx  # noqa: E402

from mafhum_api.services.documents.clamd import ClamdScanner  # noqa: E402
from mafhum_api.services.documents.scanning import ScanVerdict  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3310)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    scanner = ClamdScanner(host=args.host, port=args.port, timeout_seconds=args.timeout)
    if not scanner.available:
        print("::error::clamd did not answer PING — cannot verify detection", flush=True)
        return 1

    problems: list[str] = []

    def scan(path: Path, expected: ScanVerdict, label: str) -> None:
        with path.open("rb") as handle:
            verdict = scanner.scan_stream(handle, filename=path.name, size=path.stat().st_size)
        ok = verdict is expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {verdict.value}", flush=True)
        if not ok:
            problems.append(f"{label}: expected {expected.value}, got {verdict.value}")
            print(f"::error::{label}: expected {expected.value}, got {verdict.value}", flush=True)

    with tempfile.TemporaryDirectory(prefix="mafhum-engine-check-") as directory:
        root = Path(directory)
        clean = root / "clean-control.txt"
        clean.write_text("MAFHUM clean control file\n", encoding="utf-8")
        bare = root / "eicar.com"
        bare.write_bytes(EICAR.encode("latin-1"))
        probe = build_eicar_docx(root / "eicar-probe.docx")
        if EICAR.encode("latin-1") not in probe.read_bytes():
            print("::error::EICAR payload missing from the probe artifact", flush=True)
            return 1

        scan(clean, ScanVerdict.CLEAN, "clean control file returns CLEAN")
        scan(bare, ScanVerdict.INFECTED, "bare EICAR returns INFECTED")
        scan(probe, ScanVerdict.INFECTED, "EICAR DOCX probe returns INFECTED")

    if problems:
        print(f"RESULT: {len(problems)} ENGINE EICAR CHECK(S) FAILED", flush=True)
        return 1
    print("RESULT: ALL REAL-ENGINE EICAR CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
