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

## Phase C — Neon PostgreSQL integration — ✅ code-side; real acceptance ⛔ owner

- New settings `DB_CONNECT_TIMEOUT_SECONDS` (default 10, fail-fast on
  unreachable databases) and `DB_POOL_RECYCLE_SECONDS` (default −1 = disabled,
  preserving container behavior; set just below the provider's idle limit for
  Neon) wired into the engine; both documented in `.env.example`.
- Engine propagation regression-tested (`tests/test_vercel_deployment.py`):
  pool bounds, pre-ping, recycle, and the psycopg `connect_timeout` reaching
  the pool creator.
- Transaction/rollback semantics (`unit_of_work`), credential-free health
  reporting, and migration-as-release-step controls were audited and are
  unchanged.
- `docs/NEON_DATABASE_RUNBOOK.md`: exact env vars, TLS requirement, pooled vs
  direct endpoints, migration runbook, acceptance checklist, backup/restore.
- **Real Neon acceptance: BLOCKED — OWNER ACTION REQUIRED** (no paid resources
  or credentials in this session). Distinction kept explicit: everything
  above is verified against real PostgreSQL (pgserver/CI service), which is
  *not* a Neon acceptance test.

## Phase D — Vercel Blob private storage — ✅ adapter complete; real-store acceptance ⛔ owner

- **Shared backend contract** (`tests/test_storage_backend_contract.py`): the
  same behavioral suite runs against `LocalFileStorage` and
  `VercelBlobStorage` — round trips, empty uploads, over-limit refusal,
  interrupted chunk streams leaving no object, no-overwrite, invalid-key
  refusal, not-found mapping, idempotent delete, delete-many reporting,
  report-only orphan reconciliation.
- **Found and fixed a real divergence:** `LocalFileStorage.write` published
  with `os.replace`, silently overwriting an existing key — contradicting its
  own docstring ("never clobber an existing object") and the Blob backend's
  fail-closed contract. Publishing now uses a hard link (`os.link`), which
  fails closed with `StorageError("storage key already exists")`; temp files
  still never survive failures. One test that relied on the old
  overwrite-as-corruption-simulation was updated to delete-then-write.
- **Provider failure paths** (`tests/test_vercel_blob_storage.py`): transient
  failures (timeout, 503, 429, connection reset) map to `StorageError` with
  nothing published; `BlobContentLengthError` mapping; malformed provider
  metadata (pathname mismatch, negative/non-int size) rejected; delete-many
  partial failure keeps failed keys for reconciliation.
- SDK review recorded: pinned `vercel-blob==0.5.0` sync surface matches the
  adapter protocol; `delete_blob` accepts 1–1000 pathnames per call (unused —
  per-key deletion is deliberately conservative); no client-upload-token API
  (constrains Phase E).
- **Real-store acceptance: BLOCKED — OWNER ACTION REQUIRED** (store
  provisioning + token are owner actions; fake-client tests are explicit test
  doubles, not real-service claims).

## Phase E — Direct upload architecture — ✅ backend; frontend wiring in Phase J

Constraint discovered during the Phase-A SDK review: the pinned Python SDK
(`vercel-blob==0.5.0`) has **no client-upload-token minting API** (it only
recognizes the `client_token_pathname_mismatch` service error), so the
direct-to-Blob browser flow of the JS SDK is not available to this backend.
The SDK's `multipart_upload_blob` is a single-process context manager and
cannot span serverless invocations.

Implemented architecture — **API-mediated chunked upload sessions**:

- `POST /api/v1/documents/upload-sessions` — authenticated, CSRF-guarded
  intent: filename/content-type validated against the same allowlist,
  declared size capped by `MAX_UPLOAD_MB`, soft quota check, server-chosen
  opaque final key, part size (`UPLOAD_PART_SIZE_BYTES`, default 4 MiB,
  under the ~4.5 MB platform body limit) and TTL
  (`UPLOAD_SESSION_TTL_MINUTES`). The response exposes only the session id
  and the splitting plan — never a storage key.
- `PUT /upload-sessions/{id}/parts/{n}` — one bounded part per request,
  raw octet-stream. Ownership is enforced before any byte is stored; the
  endpoint refuses a body larger than one part *while reading it* (no
  unbounded buffering on self-hosted deployments); duplicate part numbers
  are refused (replay protection); parts are immutable once accepted.
- `POST /upload-sessions/{id}/finalize` — verifies part count and byte
  total against the declaration, assembles the parts server-side into the
  final object (streamed, never whole-file buffered), then hands the
  assembled object to the *same* registration pipeline as a direct upload:
  magic-number check, digest/size verification, structural validation,
  **malware scan**, hard locked quota enforcement, document + processing
  job rows. Rejected finalizes compensate the assembled object AND expire
  the session with its stored parts (the parts are the rejected bytes).
  Part objects are deleted after successful assembly.
- `DELETE /upload-sessions/{id}` — explicit abort; parts removed.
- Expiry: on-access expiry plus `cleanup_expired()` (bounded, idempotent)
  for the durable-processing tick (Phase F).
- Database: additive migration `0010_upload_sessions` (sessions + parts,
  CHECK constraints, unique part numbers); round-trip
  upgrade→downgrade→upgrade verified.

Verified by 31 tests in `tests/test_upload_sessions.py`: full-flow parity
with the single-request path (same document, same processing outcome),
cross-user isolation with no existence oracle (404 shape identical),
CSRF on all mutations, oversized declared/part sizes, out-of-range part
numbers, replayed parts and double finalize, incomplete uploads, expired
intents (parts reclaimed, state persisted even when the triggering request
fails), abort, bounded idempotent cleanup, corrupt-content and
signature-mismatch rejection through the same gate, **EICAR-signature
rejection with zero rows and zero stored objects** (stub clamd — same
boundary as the existing scanning tests: application wiring, not engine
detection), and hard quota enforcement at finalize.

The single-request upload path remains available and unchanged (it stays
under the platform limit for small files); the frontend will choose the
chunked path for large files in Phase J.

## Phase F — Durable background processing — ✅ tick driver; resident worker unchanged for containers

The per-job machinery was already durable and cross-process safe
(`SELECT … FOR UPDATE SKIP LOCKED` claims, queued→running in the claim
transaction, heartbeats, generation-based retry idempotency, bounded
retries, terminal `failed` document state with machine-readable
`error_code` — the document row is the dead-letter record — and
`recover_stale_jobs` for crash recovery). What serverless lacked was a
*driver*: the resident dispatcher is a thread pool that cannot live in a
Function.

Implemented the **processing tick** — a bounded, idempotent pass:

- `POST /api/v1/processing/tick` — authenticated user + CSRF (the frontend
  fires one after an upload finalizes; self-driving serverless flow).
- `POST /api/v1/processing/tick-scheduler` — unattended bearer token
  (`PROCESSING_TICK_TOKEN`; **disabled outright — 404 — while unset**, and a
  bearer header can never fall back to a cookie session). Driven by
  `.github/workflows/processing-tick.yml` (every 5 min; **self-skipping
  no-op until the owner sets `vars.MAFHUM_API_URL` and
  `secrets.PROCESSING_TICK_TOKEN`**).
- One pass: `recover_stale_jobs` → `cleanup_expired` upload sessions →
  claim/process up to `PROCESSING_TICK_MAX_JOBS` (default 8) within
  `PROCESSING_TICK_BUDGET_SECONDS` (default 25 s, inside the Function
  execution limit). Overlapping ticks cannot double-process (exclusive
  claims); the stopped reason is reported accurately (a final probe
  distinguishes `max_jobs` from a genuinely `drained` queue) so callers
  know whether to tick again. Responses are counters only.
- **Single-driver rule:** both endpoints refuse (409) while
  `PROCESSING_WORKER_ENABLED=true` — the worker lease's single-writer
  reasoning stays intact; serverless sets it false (already the case in
  the Phase-1 Vercel project env).

Verified by 14 tests in `tests/test_processing_tick.py`: authenticated
tick processes a queued document to `ready`; drained re-tick is a no-op;
401/CSRF refusals; counters carry no document data; failures counted not
raised (missing source file → `failed=1`, document `failed` with
`error_code`); max-jobs bounding with the remainder queued and a second
tick draining it; zero-budget reporting; stale running job recovered and
processed; expired upload sessions (and their stored parts) reclaimed by
the same pass; 409 while the resident worker is enabled; scheduler
endpoint 404 while unset, working with a valid token and no session, 401
on a wrong token with no cookie fallback.

## Phase G — ClamAV and malware scanning — ✅ acceptance isolation; real-engine evidence ⛔ owner

The acceptance tooling (workflow `production-malware-acceptance.yml`, phases
1–6) runs only against disposable local containers, never real production
hosts. The **Phase-6 health-gate correction is accepted and in place**:
recovery now requires BOTH a successful start command AND a healthy
SCAN-READY result before any dependent recovery check runs; unhealthy
recovery skips the recovery upload, refresh-loop probe, and reuse evidence,
preserves the volume, and continues only with safe isolated cleanup —
regression-tested hermetically (45 checks in that suite), including
"unhealthy skips X" and "healthy executes every check", each asserting no
production resource was addressed.

Application-side: `malware_scan_required` fail-closed semantics unchanged;
the chunked-upload path (Phase E) now also proves the gate over the stub
clamd (zero rows, zero stored objects on FOUND).

Remaining: **real ClamAV engine evidence on the production host is
BLOCKED — OWNER ACTION REQUIRED** (Vercel cannot host the daemon; the
runbook documents an external host or re-architecture decision).

## Phase H — Document processing and retrieval — ✅ audited; parity proven for the new paths

Audit findings (existing guarantees re-verified, all suites green):

* **Grounded answers are extractive by construction**: the grounded path
  never calls a language model; it selects passages that literally appear
  in the evidence and quotes them verbatim with citations
  (`services/research/grounding.py`, `sanitize.py`). Statuses are honest
  (`grounded` / `insufficient_evidence` / `no_evidence_available`); an
  empty topic yields no evidence rather than an invention (tested).
* **Prompt-injection defense** (`services/research/sanitize.py`):
  instruction-shaped content is neutralized before reaching any prompt,
  Unicode bidi/control characters are stripped (Arabic/Latin reordering),
  untrusted content is delimiter-wrapped, and the UI renders text only.
  Unit tests cover the neutralizer; the extractive design means an injected
  instruction has nothing to act on even if it survived.
* **User isolation**: the IDOR matrix (`test_api_isolation.py`) covers
  documents/text/download/retry/delete/projects/sources with
  indistinguishable-404 guarantees; ownership is enforced in SQL
  predicates before any byte is read.

Gaps found and closed for the migration's new paths
(`tests/test_serverless_retrieval_parity.py`, 9 tests):

* A document uploaded **in chunks and processed by a tick** (no resident
  worker) produces byte-identical segments and the same grounded, cited
  answers as a direct upload — every quote is asserted to literally appear
  in the stored segments of the cited document.
* Cross-user grounding: two users with near-identical documents — each
  answer cites only its own user's materials; attaching another user's
  document is refused with an indistinguishable 404.
* Anonymous access refused on every new endpoint (upload sessions, parts,
  finalize, abort, both ticks); the tick response carries counters only —
  no uuids, filenames, or keys (asserted by shape and content).

## Phase I — PPTX generation — ✅ audited; serverless-safe by construction

Audit findings:

* **No background dependency.** Presentation jobs are generated
  *synchronously inside the request* (`PresentationService.create_job`:
  running → generate → artifact → succeeded/failed, inline). There is no
  worker/queue involvement, so nothing about the serverless deployment
  changes the PPTX path.
* **Pure-Python generator.** `python-pptx==1.0.2` has no system
  dependencies and no native extensions — it runs in the Vercel Python
  runtime. Deck sizes are bounded by `PresentationLimits`.
* **Storage-backend agnostic.** Artifacts are written through the same
  `DocumentStorage` protocol as documents (opaque server-generated keys),
  so they land in Vercel Blob when that backend is configured; downloads
  stream after an authorization check, never from a public path.
* **PowerPoint parity vs the web viewer** remains an owner-verification
  item (see `docs/PROJECT_COMPLETION_2026-10-31.md`); automated PPTX
  structural tests (12, `tests/test_presentations.py`) stay green.

Fix applied during the audit: the serverless entrypoint
(`vercel_app.create_vercel_app`) now forces the *declared*
`processing_worker_enabled` to `False` (the thread-level guarantee was
already `start_worker=False`). Previously, an env var left on would start
no threads **and** wedge the processing tick with the single-driver 409 —
a deployment with no driver at all. Regression-tested: cold start starts
no threads even when the env allows it, the declared state matches
reality, and the tick endpoint answers 200 (not 409) on the serverless
path (23 deployment tests total).

## Phase J — Frontend integration — ✅ upload flow, polling, tick; no secret leakage

* **Path selection:** files ≤ `DIRECT_UPLOAD_MAX_BYTES` (4 MiB) use the
  single-request upload; larger files use `documents.uploadChunked` —
  create session → PUT server-sized parts (octet-stream) → finalize. Both
  paths apply identical server-side validation, malware scanning, and quota
  enforcement; the threshold keeps every request under a serverless body
  limit on any deployment.
* **Processing nudge:** after an upload (and after a retry) the page fires
  one `processing.tick()`; on worker-driven deployments the tick is refused
  (409) and swallowed — the resident worker owns processing there. Status
  polling is unchanged (polls while any document is pending/processing).
* **No secret leakage:** the chunked client sends only the session id and
  part bytes; the session plan carries no storage key, path, or token. A
  dedicated test asserts no request URL or body ever contains
  `storage`/`key`/`path`/`token=`/`vercel_blob`. All requests remain
  relative (same-origin proxy), cookie-authenticated, CSRF-headed.
* **Error parity:** a rejected part surfaces as the server's safe Arabic
  `ApiError` envelope — the page shows exactly the message the API sent.

Verified: web lint clean, `tsc --noEmit` clean, 112 vitest tests pass
(+5: chunked happy path with part-order/content-type/range assertions,
no-leak request audit, rejected-part propagation, tick call + CSRF,
threshold constant), production build succeeds.

## Phase K — Observability, security, operations — ✅ code-side; production wiring ⛔ owner

* **Correlation IDs** (pre-existing, re-verified): every error response
  carries `request_id`; audit events and structured JSON logs record it.
* **Health** (pre-existing, re-verified): `/health/ready` degrades honestly
  (database, storage writability, worker state, scanner availability when
  scanning is mandatory); the serverless path reports
  `processing_worker: stopped` because that is true — the tick is the
  driver.
* **Metrics extended** (`/api/v1/admin/metrics`, token-scrapable):
  `mafhum_processing_jobs_total{status}` (queue depth for the tick era),
  `mafhum_upload_sessions_total{state}` (chunked-upload backlog), and
  `mafhum_processing_driver{mode="worker"|"tick"}` (misconfiguration
  signal). Counters only — regression-tested that identifiers, session
  ids, and storage keys never appear.
* **Runbook** (`docs/VERCEL_SERVERLESS_RUNBOOK.md`): container vs serverless
  architecture table, full env-var table (pooled vs direct Neon endpoints),
  owner deployment sequence, scheduled-tick setup (self-skipping until
  configured), alert table (queue growth, scanner-down while fail-closed,
  denial spikes), honest limitations (ClamAV cannot run on Vercel — the
  single hard blocker for production traffic; Hobby cron daily-only;
  Function budget; body limit), and an incident playbook (stuck pending,
  stale jobs, abandoned uploads, suspended Blob store, bad release).

Production wiring — scraping, alert delivery, scheduled tick secrets — is
**BLOCKED — OWNER ACTION REQUIRED** (needs the real deployment's URLs and
tokens; nothing was executed against production).

## Phase L — Production-readiness audit — ✅ `docs/PRODUCTION_READINESS_REPORT.md`

Written with evidence-based PASS / PARTIAL / BLOCKED / FAIL classifications
for every phase and requirement; nothing is PASS without a real integration
test behind it, test doubles are named as such, and every BLOCKED item
carries exact owner instructions (§6 of the report).

(Detailed per-phase notes are appended below as each phase completes.)
