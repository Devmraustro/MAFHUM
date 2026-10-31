"""Local authenticated DAST for MAFHUM — executed against a real, locally booted stack.

This tool executes the security-test scope of ``docs/PENTEST_READINESS.md`` §4
against a **local** deployment that it boots itself: a real PostgreSQL (pgserver
binaries), a real uvicorn API in **production posture** over self-signed HTTPS
(``APP_ENV=production``, ``COOKIE_SECURE=true``, ``MALWARE_SCAN_REQUIRED=true``,
a scoped ``METRICS_SCRAPE_TOKEN``), synthetic accounts and synthetic documents
only. No public origin, no third-party service, no real personal data and no
real malware (the EICAR test string only) is ever involved.

What is deliberately NOT claimed
--------------------------------
* The clamd daemon used by the "engine" instance is a **protocol stub** that
  answers PING/INSTREAM over TCP. It proves MAFHUM's wiring, verdict handling
  and fail-closed policy — it is NOT a detection engine and proves nothing
  about ClamAV itself. Real-engine EICAR evidence lives in the
  ``production-malware-acceptance.yml`` CI workflow.
* This is a scripted scan, not an interactive ZAP/Burp proxy session, and TLS
  termination (HSTS/CSP at Caddy) is verified at the configuration level only.
* Login throttling is verified up to the documented limit and then STOPPED —
  no brute-forcing beyond the throttle, no load testing (rules of engagement §3).

Two API instances are booted against one database:

* **A** (https://127.0.0.1:8443) — engine stub reachable, processing worker on:
  exercises the full authenticated data path (upload -> scan -> process ->
  isolation -> export -> deletion).
* **B** (https://127.0.0.1:8444) — engine unreachable, worker off: exercises
  the fail-closed malware policy, honest health disclosure and scoped metrics.

Usage:
    .venv/bin/python tools/dast/local_dast.py [--keep]

``--keep`` leaves the .dast/ workdir (logs, database, storage) for inspection.
Everything under .dast/ is gitignored. Exit code 0 iff every check passed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SRC = REPO_ROOT / "apps" / "api" / "src"
API_ROOT = REPO_ROOT / "apps" / "api"

sys.path.insert(0, str(API_SRC))
sys.path.insert(0, str(API_ROOT))

import httpx  # noqa: E402
from tests.factories.pdf_factory import build_pdf  # noqa: E402
from tests.fixtures_content import make_docx, make_zip_bomb  # noqa: E402
from tools.eicar_probe import EICAR, build_eicar_docx  # noqa: E402

WORKDIR = REPO_ROOT / ".dast"
ORIGIN = "https://dast.mafhum.local"
PORT_ENGINE = 8443
PORT_NO_ENGINE = 8444
STUB_CLAMD_PORT = 3311
DEAD_CLAMD_PORT = 3319

# The DAST target uses a self-signed local certificate on purpose; every
# client must deliberately skip CA verification for 127.0.0.1 only.
LOCAL_TLS_VERIFY = False

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

failures: list[str] = []
passes = 0
section_index = 0
_PG_SERVER: Any = None  # module-level so the server outlives boot_postgres()


def check(label: str, condition: bool, detail: str = "") -> bool:
    global passes
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}", flush=True)
    if condition:
        passes += 1
    else:
        failures.append(f"{label}{suffix}")
    return condition


def section(title: str) -> None:
    global section_index
    section_index += 1
    print(f"\n{section_index}. {title}", flush=True)


# ------------------------------------------------------------------- infrastructure
def boot_postgres() -> str:
    """Start a real PostgreSQL server via pgserver; return a SQLAlchemy URL."""
    global _PG_SERVER
    os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.mkdtemp(prefix="mafhum-dast-xdg-"))
    import pgserver

    _PG_SERVER = pgserver.get_server(str(WORKDIR / "pgdata"), cleanup_mode=None)
    uri = _PG_SERVER.get_uri()
    parts = urllib.parse.urlsplit(uri)
    query = urllib.parse.parse_qs(parts.query)
    socket_dir = (query.get("host") or [""])[0]
    dbname = parts.path.lstrip("/") or "postgres"
    user = parts.username or "postgres"
    if not socket_dir:
        raise SystemExit(f"no socket dir in pgserver URI: {uri!r}")
    return f"postgresql+psycopg://{user}@/{dbname}?host={urllib.parse.quote(socket_dir, safe='')}"


def stop_postgres() -> None:
    """Stop the booted PostgreSQL so nothing lingers after the run."""
    import contextlib
    import signal

    if _PG_SERVER is None:
        return
    with contextlib.suppress(Exception):
        _PG_SERVER.cleanup()
    with contextlib.suppress(OSError, ValueError):
        pid = _PG_SERVER.get_pid()
        if pid and pid > 1:
            os.kill(pid, signal.SIGTERM)


def run_migrations(database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(API_SRC)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        raise SystemExit("alembic upgrade head failed")
    print("[boot] migrations applied")


def generate_certificate(directory: Path) -> tuple[Path, Path]:
    """Self-signed localhost certificate so production cookies (Secure) work."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "dast.key"
    cert_path = directory / "dast.crt"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


class _ConnectionBuffer:
    """Byte-exact buffered reader for one clamd connection.

    The API client sends the command and the first INSTREAM chunk in separate
    ``sendall`` calls that TCP may coalesce, so the reader must keep every byte
    after the NUL command terminator for the stream phase.
    """

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self._buffer = bytearray()

    def _fill(self) -> bool:
        chunk = self._conn.recv(65536)
        if not chunk:
            return False
        self._buffer.extend(chunk)
        return True

    def read_command(self) -> bytes:
        while b"\0" not in self._buffer:
            if not self._fill():
                break
        if b"\0" not in self._buffer:
            return bytes(self._buffer)
        command, _, rest = bytes(self._buffer).partition(b"\0")
        self._buffer = bytearray(rest)
        return command

    def read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            if not self._fill():
                break
        out = bytes(self._buffer[:count])
        del self._buffer[:count]
        return out


class StubClamd(threading.Thread):
    """A clamd *protocol* stub: PING -> PONG, INSTREAM -> OK / EICAR FOUND.

    This is NOT an antivirus engine and no detection claim is based on it. It
    exists so the fail-closed wiring can be exercised under production posture
    without a real ClamAV daemon (which this environment cannot provide).
    """

    daemon = True

    def __init__(self, port: int) -> None:
        super().__init__(name="stub-clamd")
        self.port = port
        self.scanned_sizes: list[int] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(20.0)
            reader = _ConnectionBuffer(conn)
            command = reader.read_command()
            if command == b"zPING":
                conn.sendall(b"PONG\0")
            elif command == b"zINSTREAM":
                payload = self._read_stream(reader)
                self.scanned_sizes.append(len(payload))
                if _payload_carries_eicar(payload):
                    conn.sendall(b"stream: Eicar-Test-Signature FOUND\0")
                else:
                    conn.sendall(b"stream: OK\0")
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    @staticmethod
    def _read_stream(reader: _ConnectionBuffer) -> bytes:
        payload = bytearray()
        while len(payload) < 64 * 1024 * 1024:
            header = reader.read_exact(4)
            if len(header) < 4:
                break
            length = int.from_bytes(header, "big")
            if length == 0:
                break
            while length > 0:
                chunk = reader.read_exact(length)
                if not chunk:
                    return bytes(payload)
                payload.extend(chunk)
                length -= len(chunk)
        return bytes(payload)

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()


def _payload_carries_eicar(payload: bytes) -> bool:
    """EICAR in the raw bytes OR inside any (stored/deflated) ZIP member."""
    markers = (EICAR.encode(), EICAR.encode("latin-1"))
    if any(marker in payload for marker in markers):
        return True
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return any(
                any(marker in archive.read(name) for marker in markers)
                for name in archive.namelist()
            )
    except (zipfile.BadZipFile, OSError):
        return False


@dataclass
class ApiInstance:
    name: str
    port: int
    clamd_port: int
    worker: bool
    database_url: str
    cert: Path
    key: Path
    metrics_token: str
    process: subprocess.Popen | None = None
    log_path: Path = field(default_factory=lambda: Path("/dev/null"))

    @property
    def base_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    def start(self) -> None:
        env = {
            **os.environ,
            "APP_ENV": "production",
            "DATABASE_URL": self.database_url,
            "SESSION_SECRET": secrets.token_hex(32),
            "COOKIE_SECURE": "true",
            "ALLOWED_ORIGINS": json.dumps([ORIGIN]),
            "STORAGE_ROOT": str(WORKDIR / "storage"),
            "METRICS_SCRAPE_TOKEN": self.metrics_token,
            "MALWARE_SCAN_REQUIRED": "true",
            "CLAMD_HOST": "127.0.0.1",
            "CLAMD_PORT": str(self.clamd_port),
            "CLAMD_TIMEOUT_SECONDS": "5.0",
            "PROCESSING_WORKER_ENABLED": "true" if self.worker else "false",
            "LOG_LEVEL": "INFO",
            "PYTHONPATH": str(API_SRC),
        }
        self.log_path = WORKDIR / f"api_{self.name}.log"
        log_handle = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(  # noqa: S603 - fixed argv, repo-controlled
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mafhum_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--ssl-certfile",
                str(self.cert),
                "--ssl-keyfile",
                str(self.key),
                "--log-level",
                "info",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )

    def wait_ready(self, expect_engine: bool, timeout: float = 120.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                raise SystemExit(
                    f"{self.name} exited with {self.process.returncode}; see {self.log_path}"
                )
            try:
                response = httpx.get(
                    self.base_url + "/health/ready", verify=LOCAL_TLS_VERIFY, timeout=5
                )
                if response.status_code == 200:
                    body = response.json()
                    engine_state = body.get("checks", {}).get("malware_scanner")
                    if expect_engine and engine_state == "ok":
                        return body
                    if not expect_engine and body.get("status") in {"ok", "degraded"}:
                        return body
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1.0)
        raise SystemExit(f"{self.name} never became ready: {last_error}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


# ------------------------------------------------------------------- HTTP helpers
class User:
    """One synthetic user with their own cookie jar and CSRF token."""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self.client = httpx.Client(base_url=base_url, verify=LOCAL_TLS_VERIFY, timeout=60)
        self.email = email
        self.password = password
        self.csrf_token = ""
        self.user_id: str = ""

    def register(self) -> httpx.Response:
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": self.email,
                "password": self.password,
                "display_name": self.email.split("@")[0],
            },
            headers={"Origin": ORIGIN},
        )
        if response.status_code == 201:
            self.user_id = response.json()["user"]["id"]
            self.refresh_csrf()
        return response

    def login(self) -> httpx.Response:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            headers={"Origin": ORIGIN},
        )
        if response.status_code == 200:
            self.refresh_csrf()
        return response

    def refresh_csrf(self) -> None:
        response = self.client.get("/api/v1/auth/session")
        if response.status_code == 200:
            self.csrf_token = response.json().get("csrf_token", "")

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Origin": ORIGIN}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if extra:
            headers.update(extra)
        return headers

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.get(url, headers=self.headers(kw.pop("headers", None)), **kw)

    def post(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.post(url, headers=self.headers(kw.pop("headers", None)), **kw)

    def delete(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.delete(url, headers=self.headers(kw.pop("headers", None)), **kw)

    def upload(self, filename: str, payload: bytes, mime: str) -> httpx.Response:
        return self.client.post(
            "/api/v1/documents",
            files={"file": (filename, payload, mime)},
            headers=self.headers(),
        )


def wait_document_ready(user: User, document_id: str, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = user.get(f"/api/v1/documents/{document_id}")
        if response.status_code == 200:
            body = response.json()
            if body["status"] in {"ready", "failed"}:
                return body
        time.sleep(0.5)
    raise TimeoutError(f"document {document_id} never finished processing")


def storage_files() -> list[Path]:
    root = WORKDIR / "storage"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


# ------------------------------------------------------------------------- suites
def suite_authentication(base: str) -> User:
    section("Authentication (register / login / logout / session)")
    student = User(base, "student@example.com", "Dast-Student-Pass-1")
    response = student.register()
    check("register returns 201", response.status_code == 201, detail=str(response.status_code))
    check("register response carries no password material", "password" not in response.text.lower())

    duplicate = User(base, "student@example.com", "Other-Pass-2")
    check("duplicate email rejected", duplicate.register().status_code == 409)

    bad_password = User(base, "student@example.com", "Wrong-Password-9")
    login = bad_password.login()
    check(
        "login with wrong password is 401", login.status_code == 401, detail=str(login.status_code)
    )

    login = student.login()
    check(
        "login with correct password is 200",
        login.status_code == 200,
        detail=str(login.status_code),
    )

    session = student.client.get("/api/v1/auth/session")
    check(
        "session reflects the caller",
        session.status_code == 200 and bool(session.json().get("csrf_token")),
    )
    me = student.get("/api/v1/auth/me")
    check(
        "/auth/me returns the registered email",
        me.status_code == 200 and me.json().get("email") == student.email,
    )

    with httpx.Client(verify=LOCAL_TLS_VERIFY, timeout=10, base_url=base) as anonymous:
        response = anonymous.get("/api/v1/auth/me")
        check("unauthenticated /auth/me is 401", response.status_code == 401)

    logout = student.post("/api/v1/auth/logout")
    check("logout is 204", logout.status_code == 204, detail=str(logout.status_code))
    after = student.client.get("/api/v1/auth/session")
    check("session invalid after logout", after.status_code == 401, detail=str(after.status_code))
    return student


def _is_csrf_rejection(response: httpx.Response) -> bool:
    """MAFHUM rejects CSRF failures as 422 with code=csrf_failed."""
    if response.status_code != 422:
        return False
    try:
        return response.json().get("code") == "csrf_failed"
    except ValueError:
        return False


def suite_session_csrf(student: User) -> None:
    section("Session + CSRF (cookie flags, Origin allowlist, double-submit)")
    login = student.login()
    set_cookies = login.headers.get_list("set-cookie")
    session_cookie = next((c for c in set_cookies if c.startswith("mafhum_session=")), "")
    csrf_cookie = next((c for c in set_cookies if c.startswith("mafhum_csrf=")), "")
    check("session cookie is set", bool(session_cookie))
    check(
        "session cookie is HttpOnly + Secure + SameSite=Lax",
        "httponly" in session_cookie.lower()
        and "secure" in session_cookie.lower()
        and "samesite=lax" in session_cookie.lower(),
        detail=session_cookie[:120],
    )
    check(
        "CSRF cookie is readable by scripts (not HttpOnly)",
        bool(csrf_cookie) and "httponly" not in csrf_cookie.lower(),
    )
    student.refresh_csrf()

    no_origin = student.client.post(
        "/api/v1/projects",
        json={"title": "no-origin-project"},
        headers={"X-CSRF-Token": student.csrf_token},
    )
    check(
        "mutation without Origin header is rejected (422 csrf_failed)",
        _is_csrf_rejection(no_origin),
        detail=f"{no_origin.status_code} {no_origin.text[:80]}",
    )

    bad_origin = student.client.post(
        "/api/v1/projects",
        json={"title": "evil-origin-project"},
        headers={"Origin": "https://evil.example", "X-CSRF-Token": student.csrf_token},
    )
    check(
        "mutation with foreign Origin is rejected (422 csrf_failed)",
        _is_csrf_rejection(bad_origin),
        detail=f"{bad_origin.status_code} {bad_origin.text[:80]}",
    )

    no_csrf = student.client.post(
        "/api/v1/projects",
        json={"title": "no-csrf-project"},
        headers={"Origin": ORIGIN},
    )
    check(
        "mutation without CSRF token is rejected (422 csrf_failed)",
        _is_csrf_rejection(no_csrf),
        detail=f"{no_csrf.status_code} {no_csrf.text[:80]}",
    )

    forged_csrf = student.client.post(
        "/api/v1/projects",
        json={"title": "forged-csrf-project"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": "forged-token-value"},
    )
    check(
        "mutation with forged CSRF token is rejected (422 csrf_failed)",
        _is_csrf_rejection(forged_csrf),
        detail=f"{forged_csrf.status_code} {forged_csrf.text[:80]}",
    )

    good = student.post("/api/v1/projects", json={"title": "dast-project"})
    check(
        "mutation with Origin + matching CSRF token is accepted",
        good.status_code in {200, 201},
        detail=str(good.status_code),
    )


def _error_shape(response: httpx.Response) -> dict[str, Any]:
    """Error envelope minus the per-request request_id (unique by design)."""
    try:
        body = response.json()
    except ValueError:
        return {"raw": response.text[:100]}
    return {key: value for key, value in body.items() if key != "request_id"}


def suite_isolation(student: User, student2: User) -> dict[str, Any]:
    section("Authorisation isolation (documents, cross-user probing)")
    seeded: dict[str, Any] = {}
    for name, payload in (
        (
            "student-one-alpha.pdf",
            build_pdf([["مقدمة البحث: مفهوم العدالة في الفكر السياسي."]]),
        ),
        (
            "student-one-beta.pdf",
            build_pdf([["الفصل الأول: تحليل النصوص المرجعية باللغة العربية."]]),
        ),
    ):
        response = student.upload(name, payload, PDF_MIME)
        if not check(
            f"seed upload {name} accepted",
            response.status_code == 201,
            detail=str(response.status_code),
        ):
            raise SystemExit(f"seed upload failed: {response.text[:500]}")
        doc = wait_document_ready(student, response.json()["id"])
        check(f"{name} processed to ready", doc["status"] == "ready", detail=doc["status"])
        seeded[name] = doc

    response = student2.upload("student-two-gamma.docx", make_docx("arabic"), DOCX_MIME)
    check("student2 upload accepted", response.status_code == 201, detail=str(response.status_code))
    doc2 = wait_document_ready(student2, response.json()["id"])
    check("student2 document processed to ready", doc2["status"] == "ready", detail=doc2["status"])
    seeded["student-two-gamma.docx"] = doc2

    target_id = seeded["student-one-alpha.pdf"]["id"]

    cross = student2.get(f"/api/v1/documents/{target_id}")
    missing = student2.get(f"/api/v1/documents/{uuid.uuid4()}")
    check(
        "cross-user document read is 404", cross.status_code == 404, detail=str(cross.status_code)
    )
    check(
        "not-owned and missing-resource responses are identical (modulo request_id)",
        cross.status_code == missing.status_code and _error_shape(cross) == _error_shape(missing),
        detail=f"{_error_shape(cross)} vs {_error_shape(missing)}",
    )

    cross_download = student2.get(f"/api/v1/documents/{target_id}/download")
    check(
        "cross-user download is 404",
        cross_download.status_code == 404,
        detail=str(cross_download.status_code),
    )

    cross_text = student2.get(f"/api/v1/documents/{target_id}/text")
    check(
        "cross-user text extraction is 404",
        cross_text.status_code == 404,
        detail=str(cross_text.status_code),
    )

    listing = student2.get("/api/v1/documents")
    items = listing.json().get("items", [])
    filenames = {item.get("display_filename") for item in items}
    check(
        "document list contains ONLY the caller's documents",
        "student-one-alpha.pdf" not in filenames and "student-two-gamma.docx" in filenames,
        detail=str(filenames),
    )

    cross_delete = student2.delete(f"/api/v1/documents/{target_id}")
    check(
        "cross-user delete is 404",
        cross_delete.status_code == 404,
        detail=str(cross_delete.status_code),
    )

    unauth = httpx.get(
        str(student.client.base_url) + f"/api/v1/documents/{target_id}",
        verify=LOCAL_TLS_VERIFY,
        timeout=10,
    )
    check(
        "unauthenticated document read is 401",
        unauth.status_code == 401,
        detail=str(unauth.status_code),
    )
    return seeded


def suite_upload_validation(student: User) -> None:
    section("Upload validation (magic bytes, MIME, size, ZIP bomb, traversal)")
    pdf_bytes = build_pdf([["نص عربي للتحقق من الصيغة."]])

    mismatch = student.upload("fake.docx", pdf_bytes, DOCX_MIME)
    check(
        "PDF bytes declared as DOCX rejected",
        mismatch.status_code == 422,
        detail=str(mismatch.status_code),
    )

    wrong_mime = student.upload("fine.pdf", pdf_bytes, "text/html")
    check(
        "PDF with text/html MIME rejected",
        wrong_mime.status_code == 422,
        detail=str(wrong_mime.status_code),
    )

    oversized = student.upload("big.pdf", b"%PDF-1.7" + b"\x00" * (21 * 1024 * 1024), PDF_MIME)
    check(
        "oversized upload rejected (413/422)",
        oversized.status_code in {413, 422},
        detail=str(oversized.status_code),
    )

    bomb = student.upload("bomb.docx", make_zip_bomb(), DOCX_MIME)
    check("ZIP-bomb DOCX rejected", bomb.status_code == 422, detail=str(bomb.status_code))

    traversal = student.upload("../../evil.pdf", pdf_bytes, PDF_MIME)
    check(
        "traversal filename rejected or neutralized",
        traversal.status_code in {201, 422},
        detail=f"status={traversal.status_code}",
    )
    if traversal.status_code == 201:
        doc = wait_document_ready(student, traversal.json()["id"])
        check(
            "traversal filename neutralized in stored metadata",
            ".." not in doc.get("display_filename", ""),
            detail=doc.get("display_filename", ""),
        )
    escapes = [p for p in (REPO_ROOT / "evil.pdf", REPO_ROOT / "apps" / "evil.pdf") if p.exists()]
    check("no file escaped the storage root", not escapes, detail=str(escapes))


def suite_malware_policy(engine: User, no_engine: User, stub: StubClamd) -> None:
    section("Malware policy (stub verdicts + fail-closed on engine outage)")
    clean = build_pdf([["مستند نظيف للفحص."]])
    accepted = engine.upload("clean-scan.pdf", clean, PDF_MIME)
    check(
        "clean upload accepted when engine answers CLEAN",
        accepted.status_code == 201,
        detail=str(accepted.status_code),
    )

    eicar_path = build_eicar_docx(WORKDIR / "eicar.docx")
    eicar_docx = eicar_path.read_bytes()
    rejected = engine.upload("eicar.docx", eicar_docx, DOCX_MIME)
    body = (
        rejected.json()
        if rejected.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    reason = (body.get("details") or {}).get("reason", "")
    check(
        "EICAR DOCX rejected with reason=malware_detected (stub verdict)",
        rejected.status_code == 422 and reason == "malware_detected",
        detail=f"status={rejected.status_code} reason={reason!r}",
    )

    outage = no_engine.upload("clean-but-engine-down.pdf", clean, PDF_MIME)
    outage_body = (
        outage.json()
        if outage.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    outage_reason = (outage_body.get("details") or {}).get("reason", "")
    check(
        "engine outage rejects the upload (fail-closed)",
        outage.status_code == 422 and outage_reason.startswith("scan_"),
        detail=f"status={outage.status_code} reason={outage_reason!r}",
    )
    check(
        "engine outage NEVER reports malware_detected or CLEAN",
        outage_reason not in {"malware_detected", "clean"} and "clean" not in outage_reason,
        detail=outage_reason,
    )

    check(
        "the engine stub actually received the uploaded bytes",
        len(stub.scanned_sizes) >= 2,
        detail=f"{len(stub.scanned_sizes)} scans, sizes={stub.scanned_sizes[:5]}",
    )

    limits = no_engine.get("/api/v1/documents/limits")
    disclosure = limits.json().get("malware_scanning_available")
    check(
        "/documents/limits honestly discloses scanner unavailable",
        limits.status_code == 200 and disclosure is False,
        detail=f"malware_scanning_available={disclosure}",
    )
    ready = httpx.get(
        str(no_engine.client.base_url) + "/health/ready", verify=LOCAL_TLS_VERIFY, timeout=10
    )
    ready_body = ready.json()
    check(
        "/health/ready reports degraded + malware_scanner unavailable",
        ready_body.get("status") == "degraded"
        and ready_body.get("checks", {}).get("malware_scanner") == "unavailable",
        detail=json.dumps(ready_body.get("checks", {})),
    )
    ready_engine = httpx.get(
        str(engine.client.base_url) + "/health/ready", verify=LOCAL_TLS_VERIFY, timeout=10
    )
    check(
        "/health/ready ok when engine answers PING",
        ready_engine.json().get("checks", {}).get("malware_scanner") == "ok",
        detail=json.dumps(ready_engine.json().get("checks", {})),
    )


def suite_admin_surface(base: str, admin: User, student: User, metrics_token: str) -> None:
    section("Admin surface + scoped metrics")
    with httpx.Client(verify=LOCAL_TLS_VERIFY, timeout=10, base_url=base) as anonymous:
        check(
            "unauthenticated admin overview is 401",
            anonymous.get("/api/v1/admin/overview").status_code == 401,
        )
        check(
            "unauthenticated metrics is 401",
            anonymous.get("/api/v1/admin/metrics").status_code == 401,
        )

    student_admin = student.get("/api/v1/admin/overview")
    check(
        "non-admin gets 404 (indistinguishable from a missing route)",
        student_admin.status_code == 404,
        detail=str(student_admin.status_code),
    )

    overview = admin.get("/api/v1/admin/overview")
    check(
        "admin session reads /admin/overview",
        overview.status_code == 200,
        detail=str(overview.status_code),
    )
    audit_events = admin.get("/api/v1/admin/audit-events")
    check(
        "admin session reads /admin/audit-events",
        audit_events.status_code == 200 and "items" in audit_events.json(),
    )

    # Scoped metrics: bearer-token machine principal.
    with httpx.Client(verify=LOCAL_TLS_VERIFY, timeout=10, base_url=base) as machine:
        good = machine.get(
            "/api/v1/admin/metrics", headers={"Authorization": f"Bearer {metrics_token}"}
        )
        families = (
            {line.split()[2] for line in good.text.splitlines() if line.startswith("# TYPE")}
            if good.status_code == 200
            else set()
        )
        check(
            "valid bearer token reads metrics",
            good.status_code == 200,
            detail=str(good.status_code),
        )
        check(
            "metrics exposition contains the documented families",
            {
                "mafhum_up",
                "mafhum_documents_total",
                "mafhum_processing_pending",
                "mafhum_audit_events_total",
                "mafhum_audit_denied_total",
                "mafhum_malware_scan_required",
                "mafhum_malware_scanner_available",
            }
            <= families,
            detail=str(sorted(families))[:200],
        )
        wrong = machine.get(
            "/api/v1/admin/metrics", headers={"Authorization": "Bearer definitively-wrong-token"}
        )
        check("wrong bearer token is 401", wrong.status_code == 401, detail=str(wrong.status_code))
        elsewhere = machine.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {metrics_token}"}
        )
        check(
            "scoped token grants NOTHING outside /admin/metrics",
            elsewhere.status_code == 401,
            detail=str(elsewhere.status_code),
        )

    admin_metrics = admin.get("/api/v1/admin/metrics")
    check(
        "admin session also reads metrics (no bearer needed)",
        admin_metrics.status_code == 200,
        detail=str(admin_metrics.status_code),
    )
    wrong_bearer_with_session = admin.client.get(
        "/api/v1/admin/metrics",
        headers={"Origin": ORIGIN, "Authorization": "Bearer wrong-token-with-session"},
    )
    check(
        "wrong bearer fails loudly even with an admin session (no fallback)",
        wrong_bearer_with_session.status_code == 401,
        detail=str(wrong_bearer_with_session.status_code),
    )


def suite_export_deletion(student: User, student2: User, admin: User) -> None:
    section("Data export isolation + account deletion")
    export = student.get("/api/v1/me/export")
    check("own export returns 200", export.status_code == 200, detail=str(export.status_code))
    check("export is a valid ZIP", zipfile.is_zipfile(io.BytesIO(export.content)))
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        names = archive.namelist()
        documents_manifest = json.loads(archive.read("documents.json"))
    check("export archive is not empty", len(names) > 0, detail=f"{len(names)} entries")
    check(
        "export manifest lists the caller's documents",
        len(documents_manifest) >= 2,
        detail=f"{len(documents_manifest)} document rows",
    )
    check(
        "no ZIP entry escapes (no ../, no absolute path)",
        all(not n.startswith(("/", "\\")) and ".." not in Path(n).parts for n in names),
        detail=str([n for n in names if n.startswith(("/", "\\")) or ".." in Path(n).parts][:3]),
    )
    manifest_names = [row.get("display_filename", "") for row in documents_manifest]
    blob_entries = [n for n in names if n.startswith("files/")]
    check(
        "export contains ONLY the caller's documents (manifest + blobs)",
        all("student-two" not in n for n in manifest_names)
        and len(blob_entries) == len(documents_manifest)
        and len(blob_entries) >= 2,
        detail=f"manifest={manifest_names}, blobs={len(blob_entries)}",
    )

    before = storage_files()
    check("storage holds blobs before deletion", len(before) >= 3, detail=f"{len(before)} files")
    deleted = student2.delete("/api/v1/me")
    check(
        "account deletion returns 2xx",
        deleted.status_code in {200, 202, 204},
        detail=str(deleted.status_code),
    )
    if deleted.status_code == 200:
        check(
            "deletion reports completion",
            deleted.json().get("complete") is True,
            detail=deleted.text[:200],
        )
    session_after = student2.client.get("/api/v1/auth/session")
    check(
        "session invalid after deletion",
        session_after.status_code == 401,
        detail=str(session_after.status_code),
    )

    after = storage_files()
    removed = {str(p) for p in before} - {str(p) for p in after}
    check(
        "student2 blobs removed from storage",
        len(removed) >= 1,
        detail=f"{len(removed)} file(s) removed",
    )

    events = admin.get("/api/v1/admin/audit-events")
    items = events.json().get("items", []) if events.status_code == 200 else []
    nulled = [item for item in items if item.get("actor_id") is None]
    check(
        "audit events survive deletion with NULL actor",
        events.status_code == 200 and len(nulled) > 0,
        detail=f"{len(items)} events, {len(nulled)} with NULL actor",
    )


def suite_error_hygiene(student: User) -> None:
    section("Error hygiene (no internals in error envelopes)")
    malformed = student.client.post(
        "/api/v1/auth/login",
        content=b"{not valid json",
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "X-CSRF-Token": student.csrf_token,
        },
    )
    check(
        "malformed JSON returns 422",
        malformed.status_code == 422,
        detail=str(malformed.status_code),
    )
    leaks = ("Traceback", "SELECT ", "sqlalchemy", "/home/", "raise ")
    found = [needle for needle in leaks if needle in malformed.text]
    check("malformed-JSON body leaks no internals", not found, detail=str(found))

    hostile_id = student.get("/api/v1/documents/not-a-uuid-at-all")
    check(
        "hostile document id rejected cleanly",
        hostile_id.status_code in {404, 422},
        detail=str(hostile_id.status_code),
    )
    found = [needle for needle in leaks[:3] if needle in hostile_id.text]
    check("hostile-id body leaks no internals", not found, detail=str(found))

    unknown = student.get("/api/v1/definitely-not-a-route")
    check(
        "unknown route is a clean 404", unknown.status_code == 404, detail=str(unknown.status_code)
    )
    found = [needle for needle in leaks[:3] if needle in unknown.text]
    check("unknown-route body leaks no internals", not found, detail=str(found))

    method = student.client.patch("/api/v1/auth/login", headers=student.headers())
    check(
        "wrong HTTP method rejected cleanly",
        method.status_code in {404, 405},
        detail=str(method.status_code),
    )


def suite_login_throttle(base: str) -> None:
    section("Login throttle (verified to the documented limit, then STOP)")
    sacrificial = User(base, "throttle-target@example.com", "Real-Pass-3")
    if sacrificial.register().status_code != 201:
        check("sacrificial account registered", False)
        return
    codes = []
    with httpx.Client(verify=LOCAL_TLS_VERIFY, timeout=10, base_url=base) as client:
        for _ in range(11):  # documented limit: 10 failures / 60 s window
            response = client.post(
                "/api/v1/auth/login",
                json={"email": sacrificial.email, "password": "definitely-wrong"},
                headers={"Origin": ORIGIN},
            )
            codes.append(response.status_code)
            if response.status_code == 429:
                break
    check(
        "login throttle engages within the documented limit", 429 in codes, detail=f"codes={codes}"
    )
    # Rules of engagement: STOP here. No further attempts are made.


def suite_headers_tls_config() -> None:
    section("Transport/header posture (configuration level)")
    caddy = (REPO_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    check("Caddyfile configures HSTS", "Strict-Transport-Security" in caddy)
    check(
        "Caddyfile configures X-Frame-Options or frame-ancestors",
        "X-Frame-Options" in caddy or "frame-ancestors" in caddy,
    )
    check("Caddyfile configures a Content-Security-Policy", "Content-Security-Policy" in caddy)

    response = httpx.get(
        f"https://127.0.0.1:{PORT_ENGINE}/health/live", verify=LOCAL_TLS_VERIFY, timeout=10
    )
    check("API answers over TLS (self-signed, local only)", response.status_code == 200)
    docs = httpx.get(
        f"https://127.0.0.1:{PORT_ENGINE}/api/docs", verify=LOCAL_TLS_VERIFY, timeout=10
    )
    check(
        "API does not serve /api/docs in production",
        docs.status_code == 404,
        detail=str(docs.status_code),
    )


# ------------------------------------------------------------------------- report
def write_report(commit: str, started: float) -> Path:
    path = WORKDIR / "dast_report.md"
    elapsed = time.time() - started
    lines = [
        "# Local DAST report",
        "",
        f"- Commit under test: `{commit}`",
        f"- Window: {time.strftime('%Y-%m-%d %H:%M:%S %z')} (duration {elapsed:.0f}s)",
        "- Target: local self-signed HTTPS instances (production posture), real",
        "  PostgreSQL via pgserver, synthetic accounts/documents only.",
        "- Engine: clamd protocol STUB (no detection claim).",
        "",
        f"**Result: {passes} checks passed, {len(failures)} failed.**",
        "",
    ]
    if failures:
        lines += ["## Failures", ""]
        lines += [f"- {failure}" for failure in failures]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# -------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the .dast workdir afterwards")
    args = parser.parse_args(argv)

    warnings.filterwarnings("ignore")
    git_binary = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603 - fixed argv, repo-controlled
        [git_binary, "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True)
    print(f"== MAFHUM local DAST — commit {commit[:12]} ==")
    print(f"[boot] workdir {WORKDIR}")

    database_url = boot_postgres()
    print("[boot] postgres up (unix socket)")
    run_migrations(database_url)
    cert, key = generate_certificate(WORKDIR)
    print("[boot] self-signed certificate generated")

    metrics_token = secrets.token_hex(32)  # shared by both instances, like one deployment
    stub = StubClamd(STUB_CLAMD_PORT)
    stub.start()
    time.sleep(0.3)
    with socket.create_connection(("127.0.0.1", STUB_CLAMD_PORT), timeout=3) as probe:
        probe.sendall(b"zPING\0")
        reply = probe.recv(64)
    if not reply.startswith(b"PONG"):
        raise SystemExit(f"stub clamd broken: {reply!r}")
    print(f"[boot] clamd protocol stub listening on 127.0.0.1:{STUB_CLAMD_PORT}")

    engine_api = ApiInstance(
        "engine",
        PORT_ENGINE,
        STUB_CLAMD_PORT,
        worker=True,
        database_url=database_url,
        cert=cert,
        key=key,
        metrics_token=metrics_token,
    )
    no_engine_api = ApiInstance(
        "no-engine",
        PORT_NO_ENGINE,
        DEAD_CLAMD_PORT,
        worker=False,
        database_url=database_url,
        cert=cert,
        key=key,
        metrics_token=metrics_token,
    )
    engine_api.start()
    engine_api.wait_ready(expect_engine=True)
    print(f"[boot] instance A (engine) ready on :{PORT_ENGINE}")
    no_engine_api.start()
    no_engine_api.wait_ready(expect_engine=False)
    print(f"[boot] instance B (no engine) ready on :{PORT_NO_ENGINE}")

    base = engine_api.base_url
    exit_code = 1
    try:
        student = suite_authentication(base)
        suite_session_csrf(student)

        student2 = User(base, "student2@example.com", "Dast-Student2-Pass-4")
        check("student2 registered", student2.register().status_code == 201)
        admin = User(base, "admin@example.com", "Dast-Admin-Pass-5")
        check("admin-to-be registered", admin.register().status_code == 201)
        grant = subprocess.run(  # noqa: S603 - fixed argv, repo-controlled
            [
                sys.executable,
                str(API_ROOT / "tools" / "set_admin.py"),
                "--email",
                admin.email,
                "--role",
                "admin",
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(API_SRC)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        check(
            "admin role granted via CLI (the only path)",
            grant.returncode == 0,
            detail=(grant.stdout or grant.stderr).strip()[-200:],
        )
        admin.login()

        suite_isolation(student, student2)
        suite_upload_validation(student)

        no_engine_student = User(
            no_engine_api.base_url, "no-engine-user@example.com", "No-Engine-Pass-6"
        )
        check("user registered on instance B", no_engine_student.register().status_code == 201)
        suite_malware_policy(engine=student, no_engine=no_engine_student, stub=stub)

        suite_admin_surface(no_engine_api.base_url, admin, student, metrics_token)
        suite_export_deletion(student, student2, admin)
        suite_error_hygiene(student)
        suite_login_throttle(base)
        suite_headers_tls_config()

        exit_code = 0 if not failures else 1
    finally:
        report = write_report(commit, time.time())
        print(f"\n== RESULT: {passes} passed, {len(failures)} failed ==")
        for failure in failures:
            print(f"  FAIL: {failure}")
        print(f"[report] {report}")
        print(f"[logs]   {WORKDIR / 'api_engine.log'}, {WORKDIR / 'api_no-engine.log'}")
        no_engine_api.stop()
        engine_api.stop()
        stub.stop()
        stop_postgres()
        if not args.keep:
            print("[teardown] stopped both instances, the stub clamd and postgres")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
