# Local Windows verification report — 2026-10-30

Executor: Z Code (native Windows host) · Scope: the narrow local/owner pass of
`docs/Z_CODE_HANDOFF_2026-10-28.md` · Branch: `verification/windows-local-2026-10-29`
(= PR #6 head `1199178d1b138aeda88b4277176d72044ed271a3`, base `main` `2a15f0c616806cede4ed4a09d6a2c2669313c8f3`).

**Overall: PARTIAL.** Priority A (native Windows backend verification) ran for
real and passed with one environment-caused test failure, fully diagnosed.
Priorities B, C, D are **BLOCKED** by missing host software and missing owner
inputs. Priority E was correctly **NOT PERFORMED** (no written authorisation).
The project remains **NOT production-ready**, and this report does not claim
otherwise.

---

## 0. Recovery of the interrupted session

The previous execution died with an HTTP 502 before reporting. State was
inspected before any action; nothing was reset, cleaned, checked out over, or
deleted.

| Location | State found |
|---|---|
| `…\draft-73627e5c-…\MAFHUM` | Branch `arena/1cb7b9d2-mafhum` @ `2719689fd23a508fe2ad8bfb5bc7ff6dffa9cb6f` (that branch's commits are reachable from `main` — PR #1's head is an ancestor of `origin/main`), in sync with origin, **one uncommitted modified file** (see §0.1). |
| `…\draft-73627e5c-…\MAFHUM-verify-windows` | Git worktree on branch `verification/windows-local-2026-10-29` @ `1199178d1…` (exactly PR #6's head), **clean** — the crashed session had created the verification branch/worktree but committed nothing yet. |
| `origin` | `main` = `2a15f0c…` (unchanged from checkpoint). PR #6 per GitHub API: `state=open`, `merged=false`, `mergeable=true`, `mergeable_state=clean`, head `1199178d1b13`, base `2a15f0c61680`. Only PRs 1–6 exist in this repository. |
| Shared workspace folder | `pr10.diff` / `pr10files.json` belong to a different repository (`Devmraustro/AUSTRO-AI` PR #10) — unrelated leftovers, not touched. |

### 0.1 Preserved uncommitted change (crashed session's work)

`MAFHUM\apps\api\tests\test_config_contract.py` carries an uncommitted addition
(+68 lines): a `TestDockerComposeContract` class pinning the compose-file fixes
introduced by commit `75d5d73` (env vars the app actually reads, a valid
`APP_ENV` default, a healthcheck route the app serves, `${VAR:?}`-enforced
secrets, `pg_isready -d`). It was **left exactly where found, uncommitted, and
not modified**. It was validated instead of discarding or re-implementing it:

| Check | Command (abbreviated) | Result |
|---|---|---|
| On its own checkout (`2719689` + WIP) | `python -m pytest -q -p no:cacheprovider tests/test_config_contract.py -rf` with `PYTHONPATH="src;."` | **11 passed**, exit 0 |
| New tests against PR #6's `docker-compose.yml` (same test module, `REPO_ROOT` pointed at `1199178`'s worktree) | 5 `TestDockerComposeContract` tests executed directly | **5 passed, 0 failed** |

The tests are valid and portable to the review branch; committing them is left
to the coding agent that owns that file.

---

## 1. Task matrix

| Priority | Task | Status |
|---|---|---|
| A1 | Clean-room walkthrough of `docs/WINDOWS_SETUP.md` | **PASS** (2 corrections proposed — §3) |
| A2 | Database URL portability + backup/restore tests on Windows | **PASS** (25 passed, exit 0) |
| A3 | `pg_dump`/`pg_restore` version match vs the pgserver cluster | **PASS** (16.2 / 16.2 vs server 16.2) |
| A4 | Full backend suite on Windows | **PASS with 1 environment-caused failure** (941 passed, 1 failed, 24 skipped — failure diagnosed, security property proven separately; §2.4) |
| B5–B6 | Production ClamAV container healthy + real-engine 3-case malware acceptance | **BLOCKED** — no Docker engine or WSL2 on this host (§4) |
| B-extra | Production clamd files inspection | **DONE** (`deploy/clamd/*` reviewed; a Windows build-breaker defect found — §4.2) |
| C | Both decks in real Microsoft PowerPoint | **BLOCKED** — PowerPoint not installed (§5); structural checks done locally instead |
| D | Prometheus/Alertmanager scraping, rules, real delivery | **BLOCKED** — no Docker host, no owner token/destination (§6) |
| E | Authenticated DAST / pentest | **NOT PERFORMED** — no written authorisation (§7) |

No check in this report was marked PASS without having run.

---

## 2. Priority A — native Windows verification (all commands real)

### 2.1 Environment

| Item | Value |
|---|---|
| OS | Microsoft Windows 11, `[Version 10.0.26300.9711]` (`cmd /c ver`) |
| Shell | Git Bash (`git version 2.55.0.windows.5`); `core.autocrlf` resolves to `true` on this machine |
| Python | `py -3.12` → 3.12.10 (64-bit) used for the venv; 3.11 and 3.14 also installed |
| Key packages | pgserver 0.1.4, psycopg 3.3.6 + psycopg-binary 3.3.6, SQLAlchemy 2.1.6, alembic 1.20.0, fastapi 0.159.1, pytest 9.1.1, python-pptx 1.0.2, PyYAML 6.0.3, bandit 1.9.4, ruff 0.17.1, pip-audit 2.9.0 |
| Node / npm | v26.8.1 / 11.19.0 (used only for `npm ci` in `apps/slides`) |
| Docker / WSL2 | **absent** (exact evidence in §4) |
| PowerPoint / LibreOffice | **absent** (exact evidence in §5) |

### 2.2 Setup, exactly as documented (exit codes recorded)

| Command (from `MAFHUM-verify-windows`) | Exit |
|---|---|
| `py -3.12 -m venv .venv` | 0 |
| `.venv/Scripts/python.exe -m pip install --upgrade pip "setuptools>=84.1.0" wheel` | 0 (pip 26.2.2, setuptools 84.2.1) |
| `.venv/Scripts/python.exe -m pip install -e "apps/api[dev]"` | 0 |
| `cd apps/slides && npm ci` | 0 |

No step of `WINDOWS_SETUP.md` §2 failed and no undocumented workaround was
needed. The `setuptools` pip-audit gotcha the doc warns about did not occur
because the upgrade ran first, exactly as instructed.

### 2.3 Portability + backup/restore, pg tools, URL shape

```
cd apps/api
PYTHONPATH="src;." ../../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider \
    tests/test_database_url_portability.py tests/test_backup_restore.py -rf
→ .................................        [100%]   exit 0   (25 passed)
```

`pgserver 0.1.4` started a real, disposable PostgreSQL cluster on this host for
the backup/restore drill.

| Evidence | Value |
|---|---|
| Bundled tools | `pgserver/pginstall/bin`: `pg_dump.exe`, `pg_dumpall.exe`, `pg_restore.exe` |
| `pg_dump --version` | `pg_dump (PostgreSQL) 16.2` |
| `pg_restore --version` | `pg_restore (PostgreSQL) 16.2` |
| Cluster `SHOW server_version` | `16.2` — **major versions match**, `find_pg_tools()` needed no PATH fix |
| pgserver URI shape on Windows | `postgresql://postgres:***@127.0.0.1:52085/postgres` — plain TCP, ephemeral port, **no** `?host=` query string (password redacted), matching the doc's Windows prediction |

The URL-shape finding confirms the premise of
`test_database_url_portability.py` on real Windows hardware: the backup tests'
database-name substitution works on the TCP shape without the historic
`url.replace("/mafhum_test?…")` silent no-op.

### 2.4 Full backend suite on Windows

```
cd apps/api
PYTHONPATH="src;." ../../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -ra
→ 966 collected: 941 passed, 1 failed, 24 skipped   (≈ 11 min wall clock)
```

This is the **first native Windows run** of the suite (the checkpoint's
"961 passed, 5 skipped" was Linux CI). The arithmetic reconciles exactly:
966 total = CI's 961 + 5; the Linux sandbox's "no outbound TLS" egress skip
**ran for real here** (this host has outbound TLS), and 20 additional
Windows-conditional skips replaced it — every one with a printed reason:

- 15 × stub clamd serves a Unix domain socket; Windows has no AF_UNIX
- 4 × POSIX mode bits not representable on Windows filesystems
- 1 × machine antivirus quarantines a bare EICAR file on disk
- 3 × live network/egress conditions (1 of which *executed* here instead of skipping, see above; net 2 skipped +1 of the original 5)

#### The one failure — diagnosed, not a product defect

```
FAILED tests/test_unit_storage.py::TestResolution::test_symlink_component_rejected
OSError: [WinError 1314] A required privilege is not held by the client
    (at tests/test_unit_storage.py:81 → link.symlink_to(outside))
```

The test's **fixture setup** calls `Path.symlink_to`, which on Windows requires
elevation or Developer Mode — this account has neither, so the test errors
before its assertion runs. It lacks the skip guard the suite's other
platform-conditional tests use. This is a test-portability defect (defect **D2**
in §8), not a demonstrated weakness in `LocalFileStorage`.

Because the *property* matters more than the test, it was verified directly
with an NTFS **junction** — a reparse point Windows allows without any
privilege, and which `Path.is_symlink()` does **not** detect:

```
mklink /J <root>\aa <outside>         → exit 0
link.is_symlink() → False ; link.is_junction() → True
storage.resolve("aa/file.bin")
→ StorageKeyError: storage key escapes storage root     (rejected ✓)
normal key → resolves inside root                       (unchanged ✓)
```

The resolver's post-`resolve()` containment check (`storage.py:130-132`) rejects
the junction escape, so path-escape protection **holds on Windows**. The
symlink-component loop remains POSIX-focused defence in depth (defect **D3**,
informational).

### 2.5 Static gates re-run on Windows

| Command (from `apps/api` unless noted) | Exit | Output |
|---|---|---|
| `python -m ruff check .` | 0 | All checks passed! |
| `python -m bandit -r src -ll -q` | 0 | no medium/high findings |
| `python -m pip_audit` | 0 | No known vulnerabilities (editable `mafhum-api` skipped, as in CI) |
| `python tools/security_baseline.py` (repo root) | 0 | ALL SECURITY BASELINE CHECKS PASSED |
| `python tools/acceptance/prod_compose_contract.py` (repo root) | 0 | ALL PRODUCTION COMPOSE CONTRACT CHECKS PASSED |

---

## 3. `WINDOWS_SETUP.md` accuracy review (Priority A1)

Executed verbatim and correct: §2 (venv + install), §4 (semicolon
`PYTHONPATH`, targeted then full suite), §5 (bundled pg tool fallback — no PATH
fix needed here), §8's line-ending warning. Proposed corrections:

1. **§1 is insufficient for pre-existing clones.** Setting
   `core.autocrlf=input` before cloning does not fix a clone made with the
   Windows default `autocrlf=true` (the state found on this machine), and the
   repo has **no `.gitattributes`** to enforce LF. Working-tree shell scripts
   are CRLF right now (proof in §4.2). Add the `.gitattributes` fix (D1) and a
   `git ls-files --eol` verification step to the doc.
2. **§4's expectation needs one caveat.** "all tests pass with a small number
   of platform-conditional skips" is accurate only on a host that can create
   symlinks (admin / Developer Mode). On an unprivileged host,
   `test_symlink_component_rejected` errors (D2). Say so until D2 is fixed.
3. Minor: §1's stated Windows pgserver shape reads
   `postgresql+psycopg://user@127.0.0.1:PORT/postgres`; raw
   `pgserver.get_uri()` returns `postgresql://…` (no driver suffix) — the
   `+psycopg` form is the application's `DATABASE_URL` convention. Wording
   only; no functional impact (D4).

---

## 4. Priority B — real production ClamAV verification: **BLOCKED**

### 4.1 Exact reason

| Check | Command | Result |
|---|---|---|
| Docker CLI | `docker --version` | `command not found` |
| Docker Desktop install dir | `ls "C:\Program Files\Docker\Docker\resources\bin"` | No such file or directory |
| WSL2 | `wsl -l -v` | "Le Sous-système Windows pour Linux n'est pas installé" (WSL not installed) |

`deploy/docker-compose.prod.yml`'s `clamd` service (image `mafhum-clamd:0.3.0`,
built from `deploy/clamd/Dockerfile`) could therefore not be built or started,
and the three-case acceptance (clean accepted / EICAR DOCX rejected with
`reason=malware_detected` / clamd stopped → upload rejected with a `scan_*`
reason) could not be observed. **No scan result is claimed.** Per the handoff,
a CI green flag or a configuration check is not evidence, so the production
image's in-container freshclam bootstrap → SCAN-ready healthcheck → 12-hourly
refresh remains **unverified on any host** — now recorded here as the standing
blocker.

### 4.2 What was inspected anyway — and a real defect it exposed

`deploy/clamd/{Dockerfile,clamd.conf,clamdscan.conf,entrypoint.sh,healthcheck.sh}`
and `deploy/docker-compose.prod.yml` were reviewed line by line. The design is
sound: TCP-only `clamd.conf` (3310 on 0.0.0.0, internal network only), healthcheck
performs a real bounded `clamdscan` of a clean probe (exit 0 required — PING is
not trusted), entrypoint fails loudly if `freshclam` cannot produce a signature
set, refresh loop every 43 200 s, api `depends_on: service_healthy`, no engine
port published.

**Defect D1 (P1 — breaks this image on any Windows-built checkout):** with the
machine-default `core.autocrlf=true` and **no `.gitattributes`** in the repo,
the working tree shipped CRLF inside the very files the image copies:

```
git ls-files --eol deploy/clamd apps/api/entrypoint.sh
→ i/lf  w/crlf   apps/api/entrypoint.sh
→ i/lf  w/crlf   deploy/clamd/{Dockerfile,clamd.conf,clamdscan.conf,entrypoint.sh,healthcheck.sh}
$ head -c 13 deploy/clamd/entrypoint.sh → #! / b i n / s h \r \n
```

A `docker build` from this working tree would bake `#!/bin/sh\r` into the
image — the container would fail to start. (Linux CI is unaffected; this is a
Windows-checkout hazard only.) Proposed fix: add a `.gitattributes` pinning
`*.sh`, `Dockerfile`, `clamd.conf`, `clamdscan.conf` to `text eol=lf`, plus a
re- checkout/renormalise step for existing clones, documented in
`WINDOWS_SETUP.md`. Per scope, **not implemented here**.

---

## 5. Priority C — Microsoft PowerPoint visual verification: **BLOCKED**

Exact evidence of absence: no `POWERPNT.EXE` under `C:\Program Files\Microsoft
Office` (incl. `root\Office16`) or `C:\Program Files (x86)\Microsoft Office`,
no Office package in `C:\Program Files\WindowsApps`, no
`HKLM\…\App Paths\POWERPNT.EXE` registry key, no PowerPoint/Office entry in
either Start Menu Programs folder; no LibreOffice in `C:\Program Files` nor its
App Paths key. **Arabic shaping, RTL direction, clipping, chart/table layout
and editability in Microsoft PowerPoint could not be observed, and no
LibreOffice render is offered as a substitute** (the handoff explicitly forbids
that substitution).

What *was* done on Windows, so the decks are fresh and structurally verified on
this host:

```
PYTHONPATH="apps/api/src" .venv/Scripts/python.exe apps/api/tools/benchmark_pptx.py   → exit 0
check_pptx_visual.py --rtl artifacts/pptx-benchmark/benchmark_python.pptx    → ok=true, exit 0
check_pptx_visual.py --rtl artifacts/pptx-benchmark/benchmark_pptxgenjs.pptx → ok=true, exit 0
```

| Deck (regenerated locally, `artifacts/pptx-benchmark/`) | Slides | Chart | Table | RTL paragraphs | Editable | Arabic | French | Issues |
|---|---|---|---|---|---|---|---|---|
| `benchmark_python.pptx` (43 029 B) | 6 | 1 | 1 | 37 | true | true | true | none |
| `benchmark_pptxgenjs.pptx` (133 012 B) | 6 | 1 | 1 | 33 | true | true | true | none |

Observation for the eventual human pass: the pptxgenjs deck reports
`table_rtl_parts: 0` vs the python deck's `1` — worth one extra look at the
bibliography table's RTL behaviour in PowerPoint (not asserted as a defect).
Screenshots were impossible: no renderer exists on this machine; the blocked
check is the one that required them.

---

## 6. Priority D — monitoring verification: **BLOCKED**

- The stack (`deploy/docker-compose.monitoring.yml`: Prometheus v3.1.0,
  Alertmanager) is Compose-based → **no Docker engine on this host** (§4.1).
- The handoff requires the **owner's real `METRICS_SCRAPE_TOKEN`** in
  `deploy/secrets/metrics_token` (0600) — no such secret exists locally and
  none may be invented.
- Proof of **real alert delivery** requires the owner's real destination in
  `deploy/prometheus/alertmanager.yml`, which deliberately ships an
  `unconfigured` receiver. A green `amtool check-config` is explicitly not
  acceptance, so it was not run as a substitute.

Verified statically instead: `deploy/prometheus/{prometheus.yml,alert.rules.yml,alertmanager.yml}`
are present and consistent with the scoped-token design
(`prod_compose_contract.py` and `security_baseline.py` both pass — §2.5).
Scraping, rule firing and delivery remain owner-host tasks.

---

## 7. Priority E — security testing: **NOT PERFORMED — no authorisation**

No written owner authorisation naming a host, window and tester exists, so no
DAST/ZAP/Burp activity was started against anything (local, production or
third-party) — exactly as `docs/PENTEST_READINESS.md` §3 requires. The readiness
document itself was reviewed and remains accurate against this branch: rules of
engagement (§3), in/out-of-scope tables (§4) and the pre-pentest checklist (§5)
all match the repo's current CI gates. Nothing to triage; no findings invented.

---

## 8. Concrete defects for the coding agent

| ID | Priority | Defect | Evidence | Proposed fix |
|---|---|---|---|---|
| D1 | P1 | No `.gitattributes`; `core.autocrlf=true` Windows checkouts put CRLF into `deploy/clamd/*.sh`, `apps/api/entrypoint.sh`, Dockerfile/clamd confs → container builds from Windows working trees produce a broken `#!/bin/sh\r` | `git ls-files --eol` (§4.2); `head -c 13` shows `\r\n` after shebang | Add `.gitattributes` pinning those files to `text eol=lf`; document renormalisation for existing clones; add a `git ls-files --eol` check to `WINDOWS_SETUP.md` §1 and/or a CI guard |
| D2 | P2 | `tests/test_unit_storage.py::TestResolution::test_symlink_component_rejected` errors instead of skipping when Windows denies symlink creation (WinError 1314) | Full-suite failure §2.4 | Wrap `link.symlink_to(outside)` in the suite's standard platform-conditional skip (skip on `OSError` at fixture setup) |
| D3 | P3 (informational) | `Path.is_symlink()` does not detect NTFS junctions; junction-escape rejection currently relies solely on the containment check in `resolve()` | Junction experiment §2.4 | Optional: a Windows-conditional test asserting `resolve()` rejects a junction key, so the property stays pinned as coverage evolves |
| D4 | P3 (doc) | `WINDOWS_SETUP.md` §1 paraphrases the pgserver URL with a `+psycopg` driver suffix; raw `pgserver.get_uri()` returns `postgresql://…` | §2.3 | Wording correction |

Unchanged from the previous checkpoint (not re-verified here, still open):
signature-freshness monitoring, production image runtime proof (B above),
alert delivery (D), DAST (E), encryption-at-rest KMS decision, image digest
pinning, PowerPoint visual parity (C).

---

## 9. Remaining owner decisions (unchanged, restated for completeness)

Production secrets and host; domain/DNS; `METRICS_SCRAPE_TOKEN` distribution;
real alert destination; audit/backup retention policy and legal-hold ruling;
KMS/HSM choice before enabling any field encryption (ADR 0006 stays Proposed);
ClamAV/Prometheus/Alertmanager image digest pinning; build-time `MAFHUM_API_URL`
/ Vercel rebuild; repository visibility; licence (still **no LICENSE**); and
explicit written authorisation for production deployment, DNS change, Vercel
rebuild, PR #6 merge, and any pentest.

---

## 10. Production-readiness assessment

**MAFHUM is not production-ready, and this pass does not change that verdict.**
What this pass adds: the backend suite, the backup/restore drill, the pg-tool
contract and the static security gates now all pass **on a real native Windows
host** against PR #6's head, and the two Windows-specific hazards a Windows
operator would have hit (D1 build-breaker, D2 unexplained suite failure) are
precisely characterised with fixes proposed. What still blocks production, in
order: owner decisions and credentials (§9); the production ClamAV container
reaching SCAN-ready and passing the three-case acceptance on a real host (B —
still never observed anywhere); real alert delivery (D); PowerPoint visual
parity (C); and a DAST pass under written authorisation (E). PR #6 remains open,
mergeable (`mergeable_state=clean` per GitHub API), and unmerged.
