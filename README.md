# MAFHUM — مَفْهُوم

منصة تعليمية وبحثية للطلبة في الجزائر والعالم العربي. اللغة الأساسية هي العربية
بدعم كامل للاتجاه من اليمين إلى اليسار، واللغة الفرنسية ثانوية.

MAFHUM is an Arabic-first study and research platform for students in Algeria and
the wider Arab world. Arabic (with genuine RTL layout) is the primary language;
French is secondary.

**Design constraints that shape every decision here**

- Zero mandatory paid services: no paid OCR, cloud storage, or proprietary model API
  is required to install or run MAFHUM.
- Runs on ordinary hardware — no GPU.
- Navy blue and white, professional and accessible.

---

## What is actually implemented

| Area | Status |
|---|---|
| Identity: register, login, logout, sessions, CSRF | Implemented, tested |
| Projects (per-user) | Implemented, tested |
| Document ingestion: PDF + DOCX upload, validation, private storage | Implemented, tested |
| Text extraction, normalization, segmentation (Arabic + French) | Implemented, tested |
| Bounded background processing worker, retry, stale-job recovery | Implemented, tested |
| Academic source catalog with provenance tracking | Implemented, tested |
| Quotas, rate limits, cross-user isolation | Implemented, tested |
| Account and document deletion with cleanup | Implemented, tested |
| Web UI: Arabic-first RTL, French locale, document workspace | Implemented, tested |
| Grounded research: lexical retrieval, verbatim citations, insufficient-evidence status | Implemented, tested — no language model; `docs/GROUNDING_EVALUATION.md` |
| Tutor and TD tools: topics, questions, SM-2 flashcards, plans | Implemented, tested |
| Malware scanning: `clamd` socket integration, fail-closed policy | Integration implemented, tested (stub protocol tests + real ClamAV/EICAR staging gate); **no engine ships** — see limitations |
| OCR for scanned documents | **Not implemented** — see limitations |
| AI answer generation beyond grounded extraction | **Not implemented** (`ENABLE_AI_GATEWAY` exists, disabled) |

Read [`docs/ROADMAP.md`](docs/ROADMAP.md) for phase status and
[`docs/PROJECT_ARCHITECTURE.md`](docs/PROJECT_ARCHITECTURE.md) for the design.

---

## Local development

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (SQLite is **not** supported — the migrations and locking
  behaviour are PostgreSQL-specific)
- Node.js 20+ and npm

### 1. Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -e "apps/api[dev]"

cp .env.example .env          # then edit DATABASE_URL and SESSION_SECRET

# Apply migrations
.venv/bin/alembic upgrade head

# Run the API (the processing worker starts with it)
PYTHONPATH=apps/api/src .venv/bin/uvicorn mafhum_api.main:app --host 0.0.0.0 --port 8000
```

Interactive API docs are served at `/api/docs` in development only; they are
disabled when `APP_ENV=production`.

### 2. Frontend

```bash
cd apps/web
npm install
npm run dev                   # http://localhost:3000
```

The browser only ever calls **relative** `/api/v1/...` URLs. `next.config.mjs`
proxies them to `MAFHUM_API_URL` (default `http://127.0.0.1:8000`), so session
cookies stay same-origin and no backend origin is exposed to the browser.

### 3. Tests and quality gates

```bash
# Backend — from apps/api
PYTHONPATH=src:. ../../.venv/bin/pytest              # unit + integration
../../.venv/bin/ruff check .
../../.venv/bin/ruff format --check .
MYPYPATH=src ../../.venv/bin/mypy src                # strict
../../.venv/bin/pip-audit

# Frontend — from apps/web
npm run typecheck
npm run lint
npm test
npm run build
npm audit --omit=dev && npm audit
```

The backend test suite starts a real PostgreSQL server via `pgserver` (a
dev-only dependency) and runs the real Alembic migrations against it.

### 4. Live end-to-end verification

`tools/e2e/` contains the scripts used to verify a *running* system over real
HTTP — not mocks:

```bash
# Boot a disposable PostgreSQL and write its URL to .e2e/pg_url.txt
.venv/bin/python tools/e2e/boot_postgres.py

# Then start the API and run the live checks
.venv/bin/python tools/e2e/live_e2e.py
```

`.e2e/` is gitignored; it holds a real database cluster and uploaded blobs.

---

## Known limitations (deliberate, not oversights)

1. **No OCR.** A scanned or image-only PDF is detected and reported as such, but
   no text is extracted. OCRmyPDF was evaluated and rejected for the default
   install because it requires the `tesseract` binary plus language packs —
   see [`docs/OSS_REUSE_REGISTER.md`](docs/OSS_REUSE_REGISTER.md).
2. **No antivirus engine ships.** MAFHUM integrates the ClamAV `clamd` daemon
   over its socket protocol (`CLAMD_UNIX_PATH` or `CLAMD_HOST`/`CLAMD_PORT`),
   with a fail-closed switch (`MALWARE_SCAN_REQUIRED=true` rejects uploads when
   no engine vouches for them). Gate A proved detection on 2026-10-26 against a
   real engine with fresh signatures in the acceptance environment, but no
   engine or signature database is bundled. Until an operator provisions
   `clamd` in their own deployment and verifies it with the EICAR file,
   **do not describe that deployment as scanning for malware**.
   `GET /api/v1/documents/limits` discloses the live
   `malware_scanning_available` state.
3. **The processing worker pool is per-process, with one worker per database.**
   A PostgreSQL advisory lock enforces the single writer at startup. Quotas and
   the login throttle are enforced in the database and are safe across
   replicas; the bounded worker pool is not shared. Set
   `PROCESSING_WORKER_ENABLED=false` on every replica except the processing
   instance. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
4. **No URL ingestion.** Only direct file uploads are accepted; fetching a
   document from a URL is deliberately not implemented (SSRF risk).
5. **Arabic text direction.** PDF text extraction returns Arabic in logical
   order for single-script lines, but a line mixing Arabic with Latin characters
   or punctuation may not round-trip perfectly. This is a limitation of PDF text
   extraction, and it is documented rather than papered over.

See [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) and
[`docs/DATA_POLICY.md`](docs/DATA_POLICY.md) for the security and data-handling
position, including residual risks.

---

## Security reporting

Please report vulnerabilities privately to the repository maintainers rather than
opening a public issue. See [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## License and third-party components

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for every runtime,
development and test dependency with its verified license, and
[`docs/OSS_REUSE_REGISTER.md`](docs/OSS_REUSE_REGISTER.md) for the candidates
that were evaluated and rejected, with reasons.
