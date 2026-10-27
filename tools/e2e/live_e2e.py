"""Live end-to-end verification of MAFHUM over real HTTP.

Runs against a real uvicorn server, a real PostgreSQL 16.2 database and real
files on disk. Nothing here is mocked. Every step prints what the server
actually returned; the script exits non-zero on the first failed assertion.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import httpx

_API_ROOT = os.environ.get("MAFHUM_API_ROOT", "apps/api")
sys.path.insert(0, os.path.join(_API_ROOT, "src"))
sys.path.insert(0, _API_ROOT)

from tests.factories.pdf_factory import build_pdf  # noqa: E402

API = os.environ.get("MAFHUM_E2E_API", "http://127.0.0.1:8000")
ORIGIN = "http://localhost:3000"
STORAGE = Path(os.environ.get("MAFHUM_E2E_STORAGE", ".e2e/storage"))
E2E_URL = (
    os.environ.get("MAFHUM_E2E_DATABASE_URL")
    or open(os.environ.get("MAFHUM_E2E_URL_FILE", ".e2e/e2e_url.txt"), encoding="utf-8").read().strip()
)

failures: list[str] = []
step = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global step
    step += 1
    mark = "PASS" if condition else "FAIL"
    print(f"[{step:02d}] {mark}  {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def register(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post(
        f"{API}/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "طالب تجريبي"},
        headers={"Origin": ORIGIN},
    )
    response.raise_for_status()
    return response.json()["user"]


def wait_ready(client: httpx.Client, doc_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"{API}/api/v1/documents/{doc_id}", headers={"Origin": ORIGIN})
        response.raise_for_status()
        body = response.json()
        if body["status"] in {"ready", "failed"}:
            return body
        time.sleep(0.4)
    raise TimeoutError(f"document {doc_id} never left processing")


def blob_files() -> list[Path]:
    return sorted(p for p in STORAGE.rglob("*") if p.is_file()) if STORAGE.exists() else []


def main() -> int:
    # build_pdf takes pages, and each page is a *list of lines*.
    arabic_pdf = build_pdf(
        [
            [
                "مقدمة: يهدف هذا البحث إلى دراسة المصادر الأكاديمية.",
                "الفصل الأول: المنهجية المتبعة في التحليل.",
            ],
            [
                "خاتمة: تم توثيق كل مرجع بدقة.",
            ],
        ]
    )
    pdf_sha = hashlib.sha256(arabic_pdf).hexdigest()

    with httpx.Client(timeout=30.0) as client:
        # ------------------------------------------------------------ health
        section("Health and readiness")
        live = client.get(f"{API}/health/live")
        ready = client.get(f"{API}/health/ready")
        check("GET /health/live -> 200", live.status_code == 200, live.text[:90])
        check("GET /health/ready -> 200", ready.status_code == 200, ready.text[:120])

        # -------------------------------------------------------- two accounts
        section("Two independent accounts")
        user_a = register(client, "e2e.arabic@example.com", "Correct-Horse-1")
        csrf_a = client.cookies.get("mafhum_csrf")
        check("register user A -> session + CSRF cookie", bool(csrf_a), f"user_id={user_a['id'][:8]}")

        client_b = httpx.Client(timeout=30.0)
        user_b = register(client_b, "e2e.french@example.com", "Correct-Horse-2")
        csrf_b = client_b.cookies.get("mafhum_csrf")
        check("register user B -> separate session", bool(csrf_b) and csrf_b != csrf_a)

        # ------------------------------------------------------------ upload
        section("Upload a real Arabic PDF")
        upload = client.post(
            f"{API}/api/v1/documents",
            files={"file": ("بحث-التخرج.pdf", arabic_pdf, "application/pdf")},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_a or ""},
        )
        check("POST /documents -> 201", upload.status_code == 201, upload.text[:160])
        doc = upload.json()
        doc_id = doc["id"]
        check("response carries no storage path", "storage_key" not in doc and "/" not in doc.get("display_filename", ""))
        check("filename preserved for display", doc["display_filename"] == "بحث-التخرج.pdf", doc["display_filename"])

        # --------------------------------------------------------- processing
        section("Background processing")
        final = wait_ready(client, doc_id)
        check("document reaches 'ready'", final["status"] == "ready", f"status={final['status']}")
        check("page count recorded", final["page_count"] == 2, f"pages={final['page_count']}")
        check("characters extracted", final["char_count"] > 0, f"chars={final['char_count']}")
        check("language detected as Arabic", final["language_hint"] == "ar", str(final["language_hint"]))
        check("parser recorded for provenance", bool(final["parser_name"]), f"{final['parser_name']} {final['parser_version']}")
        check("not flagged as scanned", final["is_scanned"] is False)

        # --------------------------------------------------------------- text
        section("Paginated extracted text")
        text_response = client.get(
            f"{API}/api/v1/documents/{doc_id}/text",
            params={"page": 1, "page_size": 1},
            headers={"Origin": ORIGIN},
        )
        check("GET /text -> 200", text_response.status_code == 200)
        page1 = text_response.json()
        check("page_size honoured", len(page1["items"]) == 1, f"items={len(page1['items'])}")
        check("total segments > 1", page1["total"] > 1, f"total={page1['total']}")
        first = page1["items"][0]
        check("page number preserved", first["page_number"] == 1, f"page={first['page_number']}")
        check("segment is a whole line, not one glyph", len(first["text"]) > 10, f"{len(first['text'])} chars")
        check("Arabic extracted in logical order", "مقدمة" in first["text"], first["text"][:48])

        page2 = client.get(
            f"{API}/api/v1/documents/{doc_id}/text",
            params={"page": 2, "page_size": 1},
            headers={"Origin": ORIGIN},
        ).json()
        check("second page differs", page2["items"][0]["ordinal"] != first["ordinal"])

        # ----------------------------------------------------------- download
        section("Download round-trip")
        download = client.get(f"{API}/api/v1/documents/{doc_id}/download", headers={"Origin": ORIGIN})
        check("GET /download -> 200", download.status_code == 200)
        check(
            "bytes are byte-identical to the upload",
            hashlib.sha256(download.content).hexdigest() == pdf_sha,
        )
        disposition = download.headers.get("content-disposition", "")
        check("served as attachment", "attachment" in disposition, disposition[:70])
        check("not sniffable as HTML", download.headers.get("x-content-type-options") == "nosniff")

        # ------------------------------------------------- cross-user isolation
        section("Cross-user isolation (user B attacks user A's document)")
        for label, method, url in [
            ("read metadata", "GET", f"{API}/api/v1/documents/{doc_id}"),
            ("read text", "GET", f"{API}/api/v1/documents/{doc_id}/text"),
            ("download", "GET", f"{API}/api/v1/documents/{doc_id}/download"),
        ]:
            response = client_b.request(method, url, headers={"Origin": ORIGIN})
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            check(
                f"B cannot {label} -> 404 resource_not_found",
                response.status_code == 404 and body.get("code") == "resource_not_found",
                f"{response.status_code} {body.get('code')}",
            )

        delete_b = client_b.delete(
            f"{API}/api/v1/documents/{doc_id}",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_b or ""},
        )
        check("B cannot delete -> 404", delete_b.status_code == 404, str(delete_b.status_code))

        still = client.get(f"{API}/api/v1/documents/{doc_id}", headers={"Origin": ORIGIN})
        check("A's document survives B's delete attempt", still.status_code == 200)

        # A genuinely-missing document must look identical to a forbidden one.
        missing = client.get(
            f"{API}/api/v1/documents/00000000-0000-0000-0000-000000000000",
            headers={"Origin": ORIGIN},
        )
        check(
            "forbidden and missing are indistinguishable",
            missing.status_code == 404 and missing.json()["code"] == "resource_not_found",
        )

        # --------------------------------------------------------------- CSRF
        section("CSRF and origin enforcement")
        no_token = client.post(
            f"{API}/api/v1/documents",
            files={"file": ("x.pdf", arabic_pdf, "application/pdf")},
            headers={"Origin": ORIGIN},
        )
        check("mutation without CSRF token rejected", no_token.status_code == 422, str(no_token.status_code))

        bad_origin = client.post(
            f"{API}/api/v1/documents",
            files={"file": ("x.pdf", arabic_pdf, "application/pdf")},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf_a or ""},
        )
        check("mutation from foreign Origin rejected", bad_origin.status_code == 422, str(bad_origin.status_code))

        # ------------------------------------------------- malformed / hostile
        section("Hostile and malformed uploads")
        cases = {
            "empty file": b"",
            "not a PDF (plain text)": b"this is not a pdf at all",
            "PDF signature then garbage": b"%PDF-1.7\n" + b"\x00" * 64,
            "truncated PDF": arabic_pdf[: len(arabic_pdf) // 3],
            "zip bomb shape": b"PK\x03\x04" + b"\x00" * 512,
        }
        before_rows = count_documents()
        before_blobs = len(blob_files())
        for label, payload in cases.items():
            name = "hostile.pdf" if not payload.startswith(b"PK") else "hostile.docx"
            response = client.post(
                f"{API}/api/v1/documents",
                files={"file": (name, payload, "application/octet-stream")},
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_a or ""},
            )
            check(f"reject {label} -> 422", response.status_code == 422, f"{response.status_code} {response.json().get('code')}")
        check("no rows created by rejected uploads", count_documents() == before_rows, f"{before_rows} -> {count_documents()}")
        check("no orphan blobs left behind", len(blob_files()) == before_blobs, f"{before_blobs} -> {len(blob_files())}")

        # ------------------------------------------------------- quota limits
        section("Quota enforcement (limit is 3 documents per user)")
        codes = []
        for index in range(3):
            response = client.post(
                f"{API}/api/v1/documents",
                files={"file": (f"doc-{index}.pdf", arabic_pdf, "application/pdf")},
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_a or ""},
            )
            codes.append(response.status_code)
        # One document already exists, so only two more fit under the limit of 3.
        check("quota admits exactly up to the limit", codes == [201, 201, 429], str(codes))

        usage = client.get(f"{API}/api/v1/documents/usage", headers={"Origin": ORIGIN}).json()
        check("usage reports the ceiling", usage["documents"] == 3 and usage["max_documents"] == 3, str(usage))

        # ------------------------------------------------------------- delete
        section("Deletion and cleanup")
        blobs_before = len(blob_files())
        delete = client.delete(
            f"{API}/api/v1/documents/{doc_id}",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_a or ""},
        )
        check(
            "DELETE -> 200 with an explicit confirmation",
            delete.status_code == 200 and delete.json().get("deleted") is True,
            f"{delete.status_code} {delete.text[:60]}",
        )
        gone = client.get(f"{API}/api/v1/documents/{doc_id}", headers={"Origin": ORIGIN})
        check("deleted document is no longer readable", gone.status_code == 404)
        check("its blob was removed from disk", len(blob_files()) == blobs_before - 1, f"{blobs_before} -> {len(blob_files())}")
        check("no orphan rows remain", count_segments(doc_id) == 0 and count_jobs(doc_id) == 0)

        dl_after = client.get(f"{API}/api/v1/documents/{doc_id}/download", headers={"Origin": ORIGIN})
        check("deleted document cannot be downloaded", dl_after.status_code == 404)

        # ------------------------------------------------------ storage hygiene
        section("Private storage hygiene")
        check("storage root is not world-readable", oct(STORAGE.stat().st_mode & 0o777) == "0o700", oct(STORAGE.stat().st_mode & 0o777))
        modes = {oct(p.stat().st_mode & 0o777) for p in blob_files()}
        check("every blob is 0600", modes <= {"0o600"}, str(modes))
        names = [p.name for p in blob_files()]
        check("blob names are opaque hex, not user filenames", all(len(n) == 64 for n in names), str(names[:2]))

    client_b.close()

    print("\n" + "=" * 62)
    if failures:
        print(f"RESULT: {len(failures)} FAILED of {step} checks")
        for item in failures:
            print("  -", item)
        return 1
    print(f"RESULT: ALL {step} CHECKS PASSED")
    return 0


def count_documents() -> int:
    from sqlalchemy import create_engine, text

    with create_engine(E2E_URL).connect() as conn:
        return int(conn.execute(text("select count(*) from documents")).scalar_one())


def count_segments(doc_id: str) -> int:
    from sqlalchemy import create_engine, text

    with create_engine(E2E_URL).connect() as conn:
        return int(
            conn.execute(
                text("select count(*) from document_text_segments where document_id = :d"),
                {"d": doc_id},
            ).scalar_one()
        )


def count_jobs(doc_id: str) -> int:
    from sqlalchemy import create_engine, text

    with create_engine(E2E_URL).connect() as conn:
        return int(
            conn.execute(
                text("select count(*) from document_processing_jobs where document_id = :d"),
                {"d": doc_id},
            ).scalar_one()
        )


if __name__ == "__main__":
    raise SystemExit(main())
