"""Zero-dependency uptime and content probe for MAFHUM.

Runs on a GitHub Actions schedule against the deployed web origin. It checks
more than a TCP connect: the page must return 200, contain the Arabic product
name, and arrive within a deadline. No credentials, cookies or tokens are sent;
the URL is the only input and the output is a small, redacted JSON record.

Usage
-----
    python tools/monitoring/probe.py --url https://mafhum-web.vercel.app/
    python tools/monitoring/probe.py --url http://127.0.0.1:8099/ --timeout 3

Exit codes: 0 = healthy, 1 = unhealthy, 2 = invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "https://mafhum-web.vercel.app/"
EXPECTED_MARKERS = ("مَفْهُوم", "MAFHUM")
USER_AGENT = "mafhum-uptime-probe/1.0 (+github-actions)"


def probe(url: str, *, timeout: float, expected_status: int) -> dict[str, Any]:
    started = time.perf_counter()
    report: dict[str, Any] = {
        "url": url,
        "ok": False,
        "status": None,
        "elapsed_ms": None,
        "marker_found": False,
        "error": None,
    }
    # main() validates the http(s) scheme, so no file:/custom scheme reaches here.
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(256 * 1024).decode("utf-8", errors="replace")
            report["status"] = response.status
            report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            report["marker_found"] = any(marker in body for marker in EXPECTED_MARKERS)
            report["ok"] = response.status == expected_status and report["marker_found"] is True
            if not report["ok"]:
                report["error"] = "unexpected status or missing product marker"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        # Exception text can contain host details but never credentials here;
        # clamp it so a misconfigured URL cannot echo a long secret-like value.
        report["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("MAFHUM_WEB_URL", DEFAULT_URL))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--expect-status", type=int, default=200)
    args = parser.parse_args(argv)
    if not args.url.startswith(("http://", "https://")):
        print("::error::MAFHUM_WEB_URL must be an http(s) URL", flush=True)
        return 2
    if args.timeout <= 0:
        print("::error::timeout must be positive", flush=True)
        return 2

    report = probe(args.url, timeout=args.timeout, expected_status=args.expect_status)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not report["ok"]:
        print(f"::error::uptime probe failed: {report['error']}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
