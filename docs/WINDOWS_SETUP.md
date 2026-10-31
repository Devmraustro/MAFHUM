# Windows setup and verification

Date: 2026-10-28 · Status: **instructions are written from the code, not yet
executed on a Windows host.** The Arena sandbox is Linux; a native Windows
walkthrough is in the Z Code handoff. This document is the procedure that
walkthrough must verify and correct.

## Why this document exists

Two things in this repository used to assume POSIX:

1. `pgserver` returns a **Unix-socket URL with a query string** on Linux
   (`postgresql+psycopg://user@/postgres?host=/tmp/...`) but a **plain TCP URL**
   on Windows (`postgresql+psycopg://user@127.0.0.1:PORT/postgres`). The
   backup/restore test used to rewrite the database name with
   `url.replace("/mafhum_test?", "/postgres?")`, which silently did nothing on
   Windows. That is fixed by `tests/conftest.py::database_url_with_name`, which
   parses the URL with `sqlalchemy.engine.make_url` but performs the replacement
   on the original string (so the `?host=` socket path is not percent-encoded).
   `apps/api/tests/test_database_url_portability.py` covers all three URL shapes
   without needing a Windows machine.
2. Windows executable names end in `.exe`. `apps/api/tools/backup.py::_tool_in`
   already looks for both spellings.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Windows | 10 21H2+ or 11 | PowerShell 5.1+ or PowerShell 7 |
| Python | 3.11 or 3.12 (64-bit) | from python.org; tick **Add python.exe to PATH** |
| Git | current | `git config --global core.autocrlf input` — see below |
| Microsoft Visual C++ Redistributable | 2015–2022 x64 | needed by the PostgreSQL binaries in `pgserver` |
| Docker Desktop | optional | only for the Compose rehearsal; not needed for the pytest suite |
| LibreOffice | optional | only for a local PPTX render check |
| Microsoft PowerPoint | optional | the only way to check true PowerPoint visual parity |

`pgserver==0.1.4` publishes `win_amd64` wheels containing real PostgreSQL
binaries, so **no separate PostgreSQL server installation is required** to run
the test suite. The suite starts its own disposable cluster in a temp directory.

## 1. Clone and configure Git

```powershell
git config --global core.autocrlf input
git clone https://github.com/Devmraustro/MAFHUM.git
cd MAFHUM
```

`core.autocrlf input` keeps LF in the repository while allowing Windows tools to
read the files. Do not use `true`: it rewrites shell scripts and Docker
entrypoints on checkout, which breaks them inside Linux containers.

## 2. Create the environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip "setuptools>=84.1.0" wheel
python -m pip install -e "apps/api[dev]"
```

If PowerShell blocks script activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Known gotcha: a bare `pip-audit` run in a fresh venv can flag the *system*
`setuptools` (66.1.1) with four advisories. The upgrade above is the fix; CI
does the same. Do not interpret that as a MAFHUM vulnerability.

## 3. Environment variables (PowerShell syntax)

The application has **no env prefix** and reads variable names exactly as in
`.env.example`. For a test run none of these are required — pytest builds its
own `Settings` — but for a manual server they are:

```powershell
$env:APP_ENV         = "development"
$env:DATABASE_URL    = "postgresql+psycopg://mafhum:change_me@localhost:5432/mafhum"
$env:SESSION_SECRET  = "dev-only-insecure-change-me"
$env:ALLOWED_ORIGINS = '["http://localhost:3000"]'
$env:STORAGE_ROOT    = ".\data\mafhum-storage"
```

PowerShell uses `$env:NAME = value`, **not** `export NAME=value`. Single quotes
keep the JSON array's double quotes intact. Values set this way last only for
the current session; use `setx NAME value` for persistence (and restart the
terminal). Never commit a real `SESSION_SECRET` or database password.

## 4. Run the backend suite

```powershell
cd apps\api
$env:PYTHONPATH = "src;."
python -m pytest -q -p no:cacheprovider
```

`PYTHONPATH` uses a **semicolon** on Windows (`:` on POSIX). The suite uses
`pgserver` to start a real PostgreSQL, applies the real Alembic migrations and
runs the backup/restore drill against a scratch database. Expected: all tests
pass with a small number of platform-conditional skips.

To run only the portability and backup tests while diagnosing a Windows issue:

```powershell
python -m pytest -q tests/test_database_url_portability.py tests/test_backup_restore.py -rf
```

## 5. `pg_dump` / `pg_restore` version matching

`find_pg_tools()` in `apps/api/tools/backup.py` prefers a `pg_dump`/`pg_restore`
pair on `PATH`, then falls back to the binaries bundled with `pgserver`, and
selects the pair whose `pg_restore` major version matches the server. A
**version mismatch is a real failure**, not a warning: a newer `pg_restore`
emits statements an older server rejects (observed with client 17 against
server 16).

Check what is in use:

```powershell
pg_dump --version
pg_restore --version
python -c "import pgserver, pathlib; print(pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin')"
```

If the versions do not match the `pgserver` cluster (PostgreSQL 16), either:

- install the **PostgreSQL 16 client tools** for Windows and put
  `C:\Program Files\PostgreSQL\16\bin` at the front of `PATH`, or
- remove conflicting client directories from `PATH` and let `find_pg_tools`
  use the bundled pair.

Record both version strings in the verification report. A passing backup command
is not evidence that restore works — always run `test_backup_restore.py`, which
restores into a scratch database and verifies every document row's blob
checksum.

## 6. Frontend and PPTX tooling (optional)

```powershell
cd apps\web
npm ci
npm run typecheck
npm run lint
npm test
npm run build

cd ..\slides
npm ci
npm test
```

For a local render check, install LibreOffice (which provides `soffice.exe`),
add its program directory to `PATH`, install poppler-utils (or WSL), and follow
`.github/workflows/pptx-render.yml`. Note that `pdftoppm`/`pdftotext` are
poppler tools; on native Windows use a poppler build such as the one shipped
with `pdfinfo`/`pdftoppm` binaries, or run the render step inside WSL2.

**Headless LibreOffice is not PowerPoint.** To check true visual parity, open
`artifacts/pptx-benchmark/benchmark_python.pptx` and
`benchmark_pptxgenjs.pptx` in Microsoft PowerPoint on Windows and verify:

- Arabic shaping and RTL paragraph direction (no tofu boxes, no reversed text);
- mixed Arabic/French citation lines keep logical order;
- no clipped or overlapping text, correct 16:9 slide size;
- chart labels and the bibliography table's column order;
- text boxes are genuinely editable (click into one and type).

Capture screenshots with slide numbers. This is explicitly outside CI's scope.

## 7. Docker Desktop / WSL2 (optional, for Compose)

The production and rehearsal Compose files are Linux-container definitions.
With Docker Desktop in WSL2 mode they work from PowerShell, but **Caddy's ACME
challenge needs a real public domain**. For a local rehearsal use the root
`docker-compose.yml` (dev stack, no Caddy), as
`.github/workflows/deployment-rehearsal.yml` does.

```powershell
wsl --install                      # if WSL2 is not present
docker desktop status
docker compose version             # must be v2
docker compose up -d --build --wait
```

Do not point `MAFHUM_DOMAIN` at a fake domain and expect TLS: Caddy will fail
to obtain a certificate. The CI rehearsal uses the dev stack for exactly this
reason.

## 8. Known Windows limitations to record

- The `production-malware-acceptance.yml` workflow builds a Debian-based ClamAV
  image and runs Linux containers; it is a CI check, not a native Windows
  procedure.
- `clamd` Unix sockets are POSIX-only. On Windows use `CLAMD_HOST` with a TCP
  daemon (Docker Desktop, WSL2, or a network ClamAV service).
- Path handling in the API uses `pathlib` and is platform-aware, but the
  production container is Linux; Windows paths are a development concern only.
- `git` line endings matter for `deploy/clamd/*.sh` and
  `apps/api/entrypoint.sh`: CRLF in those files will break `/bin/sh` inside a
  container. Keep `core.autocrlf=input` and verify with
  `git ls-files --eol`.

## 9. What a Windows verification report must contain

Exact commands and their exit codes; `python -V`, `pip list` for the key
packages; `pg_dump --version` and `pg_restore --version`; the full pytest
summary line; the `pgserver` URI shape observed (redact any password); the
branch commit SHA; and a list of any step where these instructions were wrong,
with the corrected command. Do not report PASS for a check that was not run.
