# MAFHUM Vercel-Native Migration — Execution Phase Checklist

**Working branch:** `arena/8c435881-mafhum` (this session's branch; contains the full
stack: PR #12's ClamAV commits, PR #13's migration commits through `c39b0a7`,
and the continuation work recorded here).
**Authorization boundaries:** no paid resources, no production secrets/env
changes, no production deploys, no data migration, no merging PR #12/#13.
Anything requiring those is recorded as **BLOCKED — OWNER ACTION REQUIRED**.

Status legend: ✅ done+verified · 🟡 partial · ⛔ blocked (owner) · ⬜ not started

## Phase A — CI stabilization and configuration integrity — ✅

**Root cause of the CI failure (run 41325241643, commit `c39b0a7`):**
`.env.example` documented `BLOB_READ_WRITE_TOKEN`, but the variable was not a
`Settings` field, so `tests/test_config_contract.py::TestEnvExample::
test_every_documented_variable_is_a_real_setting` correctly refused the drift.

**Fix (configuration design, not a template edit):**

- `Settings` gained `blob_read_write_token` and `blob_store_id` — real fields,
  read from the same env vars the pinned SDK (`vercel-blob==0.5.0`) reads, so
  the documented template, fail-fast validation, and SDK credential resolution
  describe one configuration surface.
- The adapter now takes credentials explicitly. With a read-write token it
  binds the SDK's public session options (`vercel.api.session` +
  `SyncBlobServiceOptions` + `BlobCredentials`) around every operation, which
  also fixes a real drift trap: pydantic-settings reads `.env` files, the SDK
  only reads `os.environ`, so a token configured only in `.env` was previously
  invisible to the SDK. With `BLOB_STORE_ID` only (Vercel OIDC deployments)
  the SDK's own default resolution is used unchanged.
- Fail-fast: constructing the real backend without any credential source, and
  booting production with `STORAGE_BACKEND=vercel_blob` without
  `BLOB_READ_WRITE_TOKEN`/`BLOB_STORE_ID`, now refuse to start.
- Token format is validated at construction (`vercel_blob_rw_<store>_<secret>`,
  mirroring the SDK's documented format); the secret never appears in errors
  or logs.
- Not-found mapping now relies on the SDK's real hierarchy
  (`BlobNotFoundError` subclasses `FileNotFoundError`) instead of exception
  name matching; tests raise the real SDK exception.

**Evidence (local, commit on this branch):**

| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check .` (apps/api) | pass (141 files) |
| `mypy src` (strict) | pass (74 files) |
| Backend `pytest tests` | **1052 passed, 7 skipped, 0 failed** (baseline at `c39b0a7` was 1042 passed, 7 skipped, 1 failed) |
| Alembic round-trip on disposable pgserver PostgreSQL | `upgrade head` → `downgrade base` → `upgrade head` all exit 0; 20 tables |
| Bandit SAST `-ll` | exit 0 |
| Ruff security rules on `tools/` | pass |
| Monitoring validation | 81 checks pass |
| Security baseline / compose contract | pass |
| Frontend lint / typecheck / tests / build | pass / pass / **107 passed** / pass |

The 7 skipped tests are pre-existing environmental skips (ClamAV daemon,
live research sources — no Docker/networked services in this sandbox); they
are also skipped in CI's backend job.

## Phase B — Vercel-native backend architecture — ✅ (carried from Phase 1, re-verified)

- Entrypoint `api/index.py` → `mafhum_api.vercel_app.app`
  (`create_app(start_worker=False)` + PostgreSQL-only guard) unchanged and
  re-covered by `tests/test_vercel_deployment.py` (19 tests, green).
- No persistent threads at cold start; bounded pool with opt-in pre-ping;
  engine disposal on shutdown; no DDL/migrations at startup (regression-tested).
- `mafhum-api-poc` Vercel preview deployment previously built READY from this
  lineage (`docs/VERCEL_PHASE1_POC.md`); runtime HTTP probing remains blocked
  by the account's deployment protection (login required) — documented, not
  claimed as tested.
- Phase-2 additions re-verified within the full suite above.

## Phase C — Neon PostgreSQL integration — ⬜ (planned)

Code-side connection/pooling behavior is already exercised against real
PostgreSQL (pgserver locally, postgres service in CI); the Neon-specific
runbook and acceptance checklist are pending. Real Neon acceptance will be
**BLOCKED — OWNER ACTION REQUIRED** (no paid resources or credentials may be
created by this session).

## Phase D — Vercel Blob private storage — ⬜ (planned; adapter exists)

The adapter and fake-client tests exist; remaining work per
`docs/VERCEL_PHASE2_BLOB_DESIGN.md` §6 (shared contract tests, malformed SDK
responses, partial-write and timeout paths, batch deletion review). Real-store
acceptance will be **BLOCKED — OWNER ACTION REQUIRED**.

## Phase E — Direct upload architecture — ⬜ (planned)

Constraint discovered during the Phase-A SDK review: the pinned Python SDK
(`vercel-blob==0.5.0`) has **no client-upload-token minting API** (it only
recognizes the `client_token_pathname_mismatch` service error), so the
direct-to-Blob browser flow of the JS SDK is not directly available.

## Phase F — Durable background processing — ⬜ (planned)

## Phase G — ClamAV and malware scanning — ⬜ (verification planned)

## Phase H — Document processing and retrieval — ⬜ (audit planned)

## Phase I — PPTX generation — ⬜ (audit planned)

## Phase J — Frontend integration — ⬜ (verification planned)

## Phase K — Observability, security, operations — ⬜ (planned)

## Phase L — Production-readiness audit — ⬜ (`docs/PRODUCTION_READINESS_REPORT.md` at the end)

(Detailed per-phase notes are appended below as each phase completes.)
