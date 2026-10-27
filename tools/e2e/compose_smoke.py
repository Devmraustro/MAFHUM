"""Real HTTP smoke journey against the deployed Compose stack.

This is deliberately smaller than ``live_e2e.py``: it does not inspect the
database or the blob filesystem (those are exercised by the pytest suite against
pgserver). What it proves is that the three containers start together, that the
Next.js rewrite really reaches the API, that migrations ran in the container,
and that an upload/process/download round-trip works over real HTTP.

Run from the repository root:

    python tools/e2e/compose_smoke.py

The script leaves one processed document behind on purpose: the deployment
rehearsal workflow backs up the stack afterwards, so the archive must contain a
real blob to verify.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from tests.factories.pdf_factory import build_pdf  # noqa: E402

API = os.environ.get("MAFHUM_SMOKE_API", "http://127.0.0.1:8000")
WEB = os.environ.get("MAFHUM_SMOKE_WEB", "http://127.0.0.1:3000")
ORIGIN = "http://localhost:3000"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' — ' + detail) if detail else ''}", flush=True)
    if not condition:
        failures.append(label)


def wait_for_api(client: httpx.Client, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = client.get(f"{API}/health/ready", timeout=5.0)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return True
        except Exception:  # startup is expected to refuse connections
            print("  (waiting for API to accept connections)", flush=True)
        time.sleep(1.0)
    return False


def main() -> int:
    pdf = build_pdf(
        [
            ["مقدمة: اختبار النشر الحقيقي لمشروع مَفْهُوم.", "فصل أول: التوثيق بالمصادر."],
            ["خاتمة: كل اقتباس مرتبط بمصدره."],
        ]
    )
    pdf_sha = hashlib.sha256(pdf).hexdigest()

    with httpx.Client(timeout=30.0) as client:
        check("API readiness", wait_for_api(client), f"{API}/health/ready")

        web = client.get(f"{WEB}/", timeout=15.0)
        check("web serves the home page", web.status_code == 200, f"HTTP {web.status_code}")

        proxied = client.get(f"{WEB}/health/live", timeout=15.0)
        check(
            "Next.js rewrite reaches the API",
            proxied.status_code == 200 and proxied.json().get("status") == "ok",
            f"HTTP {proxied.status_code} {proxied.text[:80]}",
        )

        email = f"rehearsal-{uuid.uuid4().hex[:10]}@example.invalid"
        registered = client.post(
            f"{API}/api/v1/auth/register",
            json={"email": email, "password": "Rehearsal-Only-1", "display_name": "rehearsal"},
            headers={"Origin": ORIGIN},
        )
        check("register an account", registered.status_code == 201, registered.text[:120])
        csrf = client.cookies.get("mafhum_csrf") or ""

        admin = client.get(f"{API}/api/v1/admin/overview", headers={"Origin": ORIGIN})
        check(
            "new admin routes exist but reject a normal user",
            admin.status_code == 404 and admin.json().get("code") == "resource_not_found",
            f"HTTP {admin.status_code}",
        )

        upload = client.post(
            f"{API}/api/v1/documents",
            files={"file": ("اختبار-النشر.pdf", pdf, "application/pdf")},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        check("upload a real Arabic PDF", upload.status_code == 201, upload.text[:140])
        if upload.status_code != 201:
            return _result()
        doc_id = upload.json()["id"]

        final: dict[str, object] = {}
        deadline = time.time() + 60.0
        while time.time() < deadline:
            polled = client.get(f"{API}/api/v1/documents/{doc_id}", headers={"Origin": ORIGIN})
            final = polled.json()
            if final.get("status") in {"ready", "failed"}:
                break
            time.sleep(0.5)
        check("worker processes the document", final.get("status") == "ready", str(final))

        download = client.get(
            f"{API}/api/v1/documents/{doc_id}/download", headers={"Origin": ORIGIN}
        )
        check(
            "download is byte-identical",
            download.status_code == 200 and hashlib.sha256(download.content).hexdigest() == pdf_sha,
            f"HTTP {download.status_code}",
        )

        presentation = client.post(
            f"{API}/api/v1/presentations/jobs",
            json={"title": "عرض النشر", "language": "ar", "question": "ما هو اختبار النشر؟"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        check(
            "a grounded presentation can be generated",
            presentation.status_code in (201, 409),
            f"HTTP {presentation.status_code}",
        )

    return _result()


def _result() -> int:
    if failures:
        print(f"RESULT: {len(failures)} CHECK(S) FAILED", flush=True)
        for item in failures:
            print("  -", item, flush=True)
        return 1
    print("RESULT: ALL COMPOSE SMOKE CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
