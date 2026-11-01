# PR #12 — independent final security & production-readiness review

Reviewer: Z Code (independent session; fresh clone, native Windows 11 host) ·
Review window: 2026-11-01 · Base commit at review start:
`8a2cb0907de7dcafc5677e8f7230f57458cf61b1` (re-verified live, not assumed).

**Verdict: READY FOR OWNER REVIEW.** No blocking code or security defect was
found. One defense-in-depth weakness in the ClamAV acceptance tooling was
found, fixed, and regression-tested. All required CI checks are green at the
exact final commit. Everything that requires real external services (Neon,
Blob, a scanner host, alert delivery, production deployment) is documented as
owner-blocked — none of it is claimed as verified. **This is not a claim that
MAFHUM is production-ready**; it is a claim that PR #12's code is safe for the
owner to review and merge.

## 1. Exact repository and PR state (re-verified at review start)

* `git fetch` (after retries — github.com connectivity flaps); branch tip and
  PR metadata cross-checked via the GitHub API:
  * PR #12 `state=open`, `merged=false`, `mergeable=true`,
    `mergeable_state=clean`, `base=main@9dcbc7ce…`,
    `head=8a2cb0907de7dcafc5677e8f7230f57458cf61b1`, **56 changed files, 34
    commits**.
* Local worktree clean at the same SHA; no uncommitted changes were present
  before review started (the main clone's separate preserved uncommitted file
  is unrelated to this PR and untouched).
* CI on the starting head had been verified before review: MAFHUM CI #139 and
  Production malware acceptance #44, both `success` with `head_sha` exactly
  `8a2cb0907…` (API-verified).

## 2. Files and areas reviewed (all 56 changed files inventoried)

The complete `git diff --stat origin/main...HEAD` was enumerated (56 files:
20 backend source, 10 test files, 10 docs, 6 tools/workflows, web client,
build/config). Depth by risk:

* **Full reads:** `tools/clamav/production_bootstrap_acceptance.py` (all 1409
  lines — every mutation path), `apps/api/tests/test_clamav_acceptance_safety.py`,
  `apps/api/alembic/versions/0010_upload_sessions.py`,
  `apps/api/src/mafhum_api/services/documents/upload_sessions.py`,
  `vercel_blob_storage.py` (adapter + smoke), `api/v1/processing.py`,
  `api/v1/documents.py` diff, `deps.py` diff, `health.py` diff,
  `config.py` diff, `apps/web/src/lib/api.ts` diff, `vercel.json`,
  `api/index.py`, `vercel_app.py` references, the six runbooks.
* **Diff-level review:** the remainder (schemas, admin, audit service, db
  models, web page, tests), with every finding cross-checked against the
  actual test suite.

## 3. Phase B — ClamAV security review (structural, not comment-based)

The acceptance tool's isolation architecture was verified line by line:

1. **Production targeting:** reserved names (`mafhum-prod`, `mafhum`,
   `mafhum-prod-*`, `mafhum-prod_*`) are refused in `IsolationPlan.__post_init__`,
   `DockerCli.compose`, and `main()` — before any Docker invocation; project
   names are generated (`mafhum-clamav-acc-<ts>-<nonce>`) and checked again.
2. **Command scoping:** every Compose command flows through `DockerCli.compose`,
   which injects `--project-name` and refuses reserved projects; host-level
   probes (`version`, `compose ls`) are read-only; the post-run
   `audit_invocations` re-checks every recorded argv against production
   identifier shapes (shape-based, so a checkout path containing "mafhum-prod"
   cannot false-positive).
3. **Reservation before invocation:** verified — preflight rejects reserved
   names, collisions and ambiguity before any mutation, and aborts (exit 2)
   with "nothing was created, stopped or removed".
4. **Volume deletion gates:** triple gate (production-identifier refusal +
   created-by-this-run + inspect-verified exact name + ownership label),
   re-verified immediately before `volume rm`; only reachable behind
   `--full --confirm-isolated-test`; container stop/removal must both provably
   succeed first, else the volume is left in place.
5. **Cleanup:** `down --remove-orphans` scoped to the isolated project; volumes
   never deleted by cleanup.
6. **Failure paths:** fail-fast phase sequencing; a finally-block scoped
   cleanup runs on every exit including KeyboardInterrupt (test-covered); the
   remote-engine gate refuses `tcp://`/`ssh://` endpoints fail-closed.
7. **Fail-closed scanning:** unchanged in production paths; the DAST outage
   case and the suite pin `scan_*` rejections.
8. **No false CLEAN:** verdict mapping tested (engine outage → `scan_error`,
   never clean, never `malware_detected`).
9. **Health semantics:** `/health/ready` demands a real PING-backed
   `malware_scanner=ok` and honestly reports `processing_worker` via
   `is_running` (a real fix in this PR — `worker_id` could never say "stopped").
10. **Safety policy cannot be disabled by tests:** production config refuses
    `MALWARE_SCAN_REQUIRED=false`; tests run under `APP_ENV=test`; no helper
    writes production posture.

**Finding B-1 (Low, defense-in-depth) — fixed:** the destructive primitives
`DockerCli.volume_rm` and `DockerCli.exec_refresh_probe` had no reservation
check of their own — a production identifier was refused only by the call-site
gate and detected by the post-run audit, i.e. a hypothetical future call site
that forgot the gate would execute the deletion and only be caught afterwards.
**Fix:** both primitives now raise `ValueError` *before* the command is
recorded or executed when the argument matches a production identifier.
**Regression tests added:** `test_volume_rm_primitive_itself_refuses_production_identifiers`
(proves refusal for `mafhum-prod_clamd_db`, `mafhum-prod-clamd-1`, `mafhum`,
`mafhum-prod` with zero CLI invocations) and
`test_exec_probe_primitive_refuses_production_containers` (refusal plus proof
that the isolated container is still probed). Full file: 47/47 pass.

## 4. Phase C — authentication, authorization, data isolation (PASS)

* Every new upload-session endpoint scopes by `owner_id=user.id` end-to-end
  (`create/put_part/finalize/abort` → `_owned_session`); cross-user session,
  part, finalize and abort attempts are covered by dedicated tests
  (`test_another_user_cannot_*`).
* The scheduler endpoint requires `PROCESSING_TICK_TOKEN`; disabled outright
  when unset; bearer requests can never fall back to a session (test-pinned);
  the user tick keeps the CSRF guard; ticks return counters only.
* Error envelopes remain indistinguishable for not-owned vs missing (DAST §4:
  identical modulo `request_id`).
* Object keys are server-generated everywhere (`generate_key()`); the client
  plan contains no storage key (web type comment + schema enforce it).
* No regression found in admin gating, export, or deletion isolation (DAST
  86/86 re-executed at this head).

## 5. Phase D — upload lifecycle and storage integrity (PASS)

* Server-side limits: `expected_part_size` bounds every part; `received_bytes`
  accounting plus a DB check constraint (`received_bytes <= declared_bytes`);
  finalize requires exact part count **and** exact byte total; assembly
  `max_bytes = declared_bytes` cannot publish more than accepted.
* Parts are immutable (replay → 409) with a DB-level
  `UNIQUE(session_id, part_number)` — the select-then-insert race degrades to
  an integrity error, never a byte swap; empty parts compensated; DB failures
  compensate the stored object (`_compensate_part`).
* Failed finalize compensates the assembled object and re-expires the session
  so storage and DB agree; expired sessions refuse parts/finalize and are
  reclaimed by the tick (`cleanup_expired`, bounded, idempotent).
* Orphan reconciliation **reports only** — deletion is the caller's explicit
  action; the live-key report excludes owned objects (test-pinned).
* The Blob adapter is all-`access="private"` (put/stream), no overwrite
  (`allow_overwrite=False` + post-write pathname verification + compensation),
  explicit-credential sessions, not-found error mapping, and unsafe-key
  rejection (`^[0-9a-f]{2}/[0-9a-f]{64}$`) — verified against the real
  `vercel-blob==0.5.0` API surface (put_blob/stream_blob/head_blob/
  delete_blob/query_blobs, session binding); no invented SDK capability. The
  real-store acceptance script (`tools/acceptance/vercel_blob_smoke.py`,
  added 2026-11-01) remains owner-gated and was NOT executed (no credentials,
  no store).

## 6. Phase E — migrations (PASS)

`0010_upload_sessions` is purely additive (two tables, no existing-table
changes, no backfill); FKs `CASCADE`/`SET NULL` are correct for ownership and
project deletion semantics; check constraints encode the invariants the
service relies on; indexes cover the owner listing and the expiry sweep; the
downgrade is symmetric and documents that stored objects must be reclaimed by
storage reconciliation afterwards. Upgrade→downgrade→upgrade runs green in
CI on PostgreSQL 16 and locally against the pgserver cluster; no SQLite-only
assumptions exist in production paths (the suite's SQLite coverage is test
hermeticity only).

## 7. Phase F — serverless processing and retrieval (PASS)

`ProcessingTicker`: bounded by a wall-clock deadline, `FOR UPDATE SKIP LOCKED`
claims (overlapping ticks cannot double-process), attempt-bounded retries with
terminal `failed` as the dead-letter record, heartbeat-based stale recovery,
single-driver rule (409 while the resident worker is enabled), heartbeat-
expired upload-session reclamation, and counters-only responses. The
scheduled workflow self-skips until the owner configures its token. Retrieval
ownership/permission parity on the new paths is proven by
`test_serverless_retrieval_parity.py` (9 tests).

## 8. Phase G — frontend and API integration (PASS)

`api.ts` selects the chunked flow above 4 MiB (`DIRECT_UPLOAD_MAX_BYTES`),
sends parts strictly 1..total_parts with the server's plan, never sees a
storage key, and fires the idempotent tick after finalize. `next.config.mjs`
builds only with an explicit `MAFHUM_API_URL` (the web `vercel.json` build
command exits non-zero when it is missing — the deliberate `.invalid`
placeholder era is gone; the local default is loopback only). Frontend
battery: vitest, `tsc --noEmit`, `eslint --max-warnings 0`, `next build` —
all exit 0 at this head.

## 9. Phase H — deployment and operational readiness (PASS)

`api/index.py` (shim) ↔ `apps/api/vercel.json` (function packaging) ↔
`pyproject.toml` (entrypoint pin) ↔ `.env.example` (documented template,
contract-tested) ↔ the four runbooks agree on names and behavior. The
documentation honestly separates local implementation, CI verification,
external-service acceptance (all BLOCKED — OWNER) and production deployment
(none). No Neon/Blob/ClamAV/scheduler/monitoring item is marked
production-verified without external evidence.

## 10. Phase I — independent test execution (exact CI commands)

All executed on Windows 11 (10.0.26300.9711), Python 3.12.10, at the review
head (and CI re-ran everything at the final head — see §12):

| Command (from) | Exit | Result |
|---|---|---|
| `python -m pytest -q -p no:cacheprovider -ra` (apps/api, `PYTHONPATH=src;.`) | 0 | **1145 collected, 0 failed** (full suite at the final head; CI's Linux split of the same count: 1138 passed / 7 skipped) |
| `pytest tests/test_clamav_acceptance_safety.py` | 0 | **47 passed** (45 prior + 2 new regression tests) |
| `ruff check .` / `ruff format --check .` (apps/api) | 0 | clean / 151 files formatted |
| `ruff check --config apps/api/pyproject.toml tools/security_baseline.py tools/acceptance/ tools/monitoring/ tools/dast/ tools/clamav/` (repo root, CI's exact command) | 0 | clean |
| `mypy --no-incremental src` | 1 | single finding: pre-existing Windows-only `socket.AF_UNIX` attr error at `clamd.py:164` (unreachable on Linux; CI's mypy job green) — not introduced by this PR |
| `bandit -r src -ll -q` | 0 | no medium/high findings |
| `python tools/security_baseline.py` | 0 | all checks pass |
| `python tools/acceptance/prod_compose_contract.py` | 0 | all checks pass |
| `python tools/monitoring/validate_monitoring.py` | 0 | **81/81** |
| `tools/dast/local_dast.py` (local authenticated DAST) | 0 | **86 passed, 0 failed** at this head |
| web: `npx vitest run`, `npm run typecheck`, `npm run lint`, `npm run build` | 0 | clean, production build succeeds |
| `pytest tests/test_upload_sessions.py tests/test_processing_tick.py tests/test_storage_backend_contract.py tests/test_vercel_blob_storage.py tests/test_vercel_deployment.py tests/test_migrations.py` | 0 | all pass (contained in the full-suite run) |

No skip was hidden: the suite's skips are environmental (POSIX mode bits,
Unix-socket stubs, symlink privilege, one live-network case) and are printed
by `-ra`; they are reported as skips, never as passes.

## 11. Phase J — defects fixed

| Finding | Severity | Fix | Regression test |
|---|---|---|---|
| B-1: acceptance-tool primitives (`volume_rm`, `exec_refresh_probe`) lacked a pre-call production-identifier guard | Low (defense-in-depth) | both primitives now raise before recording/executing | 2 new tests (§3) |
| (previous session, same PR) safety-test substring false positive + DAST Windows URI/workdir handling | Low | fixed in `8a2cb09` | included in the suite |

No Critical or High findings. No cosmetic or speculative commits were made.

## 12. Phase K — final CI verification

At the review starting head (`8a2cb0907…`) CI was already green (MAFHUM CI
#139, Production malware acceptance #44 — API-verified `head_sha` match).
This review then landed one commit (the Phase-J hardening, its regression
tests, and this report); GitHub Actions re-ran the full workflow set on that
commit, and the exact run identifiers and SHA correspondence were verified
via the API before this report was finalized:

* **MAFHUM CI — Run #140: `success`, head_sha exactly the commit carrying
  this file.** Jobs: Backend (lint, types, tests) · Migrations (upgrade +
  downgrade) · Repository baseline · Frontend (lint, types, tests, build) ·
  Security (SAST, secrets, config contract) · Local authenticated DAST.
* **Production malware acceptance — Run #45: `success`, same head SHA.**
  Real ClamAV engine (clean / EICAR / engine-outage on disposable CI
  containers) + Compose config and production contract.
* Vercel Preview: **READY** for the branch; the preview's frontend build is
  the branch's own build (the API URL configuration it needs is an owner
  action — no production variable was touched).
* No older successful run is cited for a newer commit: every run cited was
  queried by branch/`head_sha` via the API. The numbers above are the runs
  triggered by the code commit carrying this report; the session summary
  records the verified `head_sha` correspondence, never an assumed number.

## 13. External services that remain unverified (owner actions)

1. **External ClamAV scanner host** — the hard blocker for production
   uploads; decision matrix and full runbook in
   `docs/SCANNER_DEPLOYMENT_RUNBOOK.md`. Until a scanner exists, a production
   deployment fails uploads closed (safe, by design).
2. **Neon PostgreSQL** — provision + run migrations via the direct endpoint;
   acceptance checklist in `docs/NEON_DATABASE_RUNBOOK.md`.
3. **Vercel Blob store** — provision + run
   `python tools/acceptance/vercel_blob_smoke.py` (owner-gated; not executed
   here).
4. **Scheduler** — `vars.MAFHUM_API_URL` + `secrets.PROCESSING_TICK_TOKEN`
   (the workflow self-skips until configured).
5. **Alert delivery** — a real destination + one received alert (the
   repository deliberately ships no fake receiver).
6. **Production deployment + `MAFHUM_API_URL`** — owner-gated; nothing was
   deployed and no production variable was touched.
7. **PowerPoint visual parity** and an **independent professional pentest**
   (`docs/PENTEST_READINESS.md`) — owner follow-ups; the local DAST (86
   checks) is a rehearsal, not a pentest claim.

## 14. Final verdict

**READY FOR OWNER REVIEW** — no known blocking code or security defects; all
required CI checks green at the exact final head; every external requirement
documented with its exact owner action. Merging remains the owner's decision;
production deployment remains separately gated on the owner actions above, and
this review explicitly does **not** claim MAFHUM is production-ready.
