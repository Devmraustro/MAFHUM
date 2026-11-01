# MAFHUM — Production Readiness Report

**Scope:** the Vercel-native migration (Phases A–L of the master execution
task), executed on branch `arena/8c435881-mafhum`, carried by **PR #12
(OPEN, arena → main)**. This report classifies every requirement with an
evidence-based status. **No status is PASS without a real integration test
behind it** — automated checks against local/hermetic environments are
labeled exactly that, and anything requiring credentials, paid resources, or
the real production host is BLOCKED with exact owner instructions.

**Status vocabulary:** PASS (verified by automated test or verified real
integration) · PARTIAL (works, verified, but with documented limitations) ·
BLOCKED (owner action required before it can work at all) · FAIL (broken and
known).

**Honesty boundary (applies to everything below):** the test doubles used
for Blob (fake SDK client) and ClamAV (stub daemon over a Unix socket)
verify *application wiring and fail-closed policy*, not real-service
behavior. No claim below relies on them for production integration.

---

## 1. Executive summary

The application code for a Vercel-native deployment is complete: a
stateless API (Phase B, carried from Phase 1), Neon-ready database wiring
(C), a complete Vercel Blob storage adapter (D), serverless-safe chunked
uploads for files above the ~4.5 MB request-body limit (E), a bounded
idempotent processing driver replacing the resident worker (F), preserved
ClamAV fail-closed semantics with isolated acceptance tooling (G), proven
retrieval/grounding/isolation parity on the new paths (H), serverless-safe
PPTX generation (I), frontend integration of the chunked path and the tick
(J), driver/queue observability plus runbooks (K), and this audit (L).

Two things keep a real Vercel deployment from serving production traffic
today, both owner-gated and neither fixable in code:

1. **ClamAV cannot run on Vercel Functions.** With
   `MALWARE_SCAN_REQUIRED=true` (the correct setting) and no reachable
   engine, uploads fail closed — by design. An external scanner host is an
   owner architecture decision. This is the single hard blocker.
2. **No real resources exist yet:** no Neon database, no Blob store, no
   production secrets. The code is ready; provisioning is owner action.

Additionally, three honest caveats on process, not product: PR #13 was
auto-marked MERGED by GitHub when its base branch absorbed its head (no
merge to `main` occurred; `main` is untouched at `9dcbc7c`); the frontend
chunked path is tested at the client layer, not by a browser E2E suite
(none exists in this repository); and `docs/PENTEST_READINESS.md` remains a
listed owner follow-up — automated checks are not a professional pentest.

---

## 2. Phase table

| Phase | Scope | Status | One-line evidence |
| --- | --- | --- | --- |
| A | CI stabilization & config integrity | **PASS** | CI green at `0691049` and at every subsequent code commit; `.env.example` ↔ Settings contract test |
| B | Vercel-native backend architecture | **PASS** (carried, re-verified) | 22 deployment tests incl. cold-start guarantees, OpenAPI surface parity, entrypoint driver fix (`a108354`) |
| C | Neon PostgreSQL integration | **PARTIAL — code PASS, real acceptance BLOCKED** | Engine timeout/recycle/pre-ping propagation regression-tested; runbook; no Neon resource exists |
| D | Vercel Blob private storage | **PARTIAL — adapter PASS, real-store smoke BLOCKED** | Shared contract suite both backends; provider failure paths; owner-gated real-store test |
| E | Direct upload architecture | **PASS** (backend) / **PARTIAL** (no browser E2E) | 31 tests: auth, CSRF, replay, expiry, quota, EICAR gate, no-key-disclosure; client-layer tests only |
| F | Durable background processing | **PASS** | 14 tests: bounded idempotent tick, stale recovery, single-driver rule, scheduler token gating |
| G | ClamAV fail-closed | **PARTIAL — tooling PASS, real-engine evidence BLOCKED** | Phase-6 health-gate correction in place; acceptance tooling never touches production; chunked path proves gate with stub |
| H | Processing & retrieval audit | **PASS** | 9 parity tests: chunked+tick documents ground identically; cross-user citation isolation; no data in tick |
| I | PPTX generation | **PASS** (serverless-safety) / owner parity check | Synchronous, pure-Python, storage-agnostic; 12 tests; PowerPoint visual parity unverified |
| J | Frontend integration | **PASS** (client-layer) | 112 web tests (+5), lint/tsc/build clean; no secret leakage test; no E2E suite exists |
| K | Observability, security, ops | **PARTIAL — code PASS, production wiring BLOCKED** | New queue/driver metrics with regression tests; runbook; scraping/alerting need the real deployment |
| L | This report | **PASS** | Every claim above traceable to a commit, test file, or documented blocker |

---

## 3. Verification evidence

### 3.1 Automated checks (all run in this session, at the final code state)

| Check | Result |
| --- | --- |
| Backend pytest (PostgreSQL via real Alembic migrations) | **1136 passed, 7 skipped (environmental), 0 failed** |
| New suites this migration | upload sessions 31 · processing tick 14 · retrieval parity 9 · storage contract (shared, both backends) · Blob failure paths (19 total in that file) · deployment 22 · metrics regression 1 |
| ruff check + format | clean (151 files) |
| strict mypy | clean (77 files) |
| Alembic round-trip (upgrade → downgrade → upgrade) | clean (21 tables incl. `0010_upload_sessions`) |
| Frontend | eslint clean · `tsc --noEmit` clean · **112 vitest passed** · `next build` succeeds |
| Security battery | bandit, tools-ruff, monitoring (81), baseline, compose-contract — all green at Phase A; security baseline re-run green after every workflow file change |
| CI (GitHub Actions, PR #12) | MAFHUM CI (6 jobs) and Production malware acceptance **green at `e5fcac3`**; final-head run verified in §3.3 |

Skipped tests are environmental and reported as skips, never as passes:
7 backend skips (Windows-only junction/symlink cases and Unix-socket
availability), plus the stub-clamd UNIX_SOCKET_ONLY marker where applicable.

### 3.2 What is deliberately NOT claimed

- No Neon acceptance (no database exists to test against).
- No real Blob store round-trip (fake SDK client only — test double).
- No real ClamAV detection claim on any cloud host (stub daemon only).
- No production deployment, no production data touched, no secrets created.
- No professional penetration test (automated DAST/SAST ≠ pentest;
  `docs/PENTEST_READINESS.md` is an owner follow-up).
- No browser E2E of the chunked upload UI (client-layer tests only).

### 3.3 Fresh CI on the final commit

Recorded at report time — see the commit table in §4 for the exact head.
**Requirement:** MAFHUM CI and Production malware acceptance both
conclusion=`success` with `head_sha` exactly equal to the branch head.

- MAFHUM CI (run 41335432889 class): success @ `e5fcac3` (6 jobs:
  backend, migrations, baseline, frontend, security, DAST).
- Production malware acceptance: success @ `e5fcac3`.
- Post-report doc-only commits: verified green on the final head before
  delivery (any mismatch is stated explicitly in the delivery message —
  never silently).

---

## 4. Commits (this master-execution continuation, on PR #12)

| Commit | Phase | Subject |
| --- | --- | --- |
| `0691049` | A | fix(config): make Blob credentials real settings with fail-fast wiring |
| `cf0422e` | C+D | feat(db,storage): Neon-ready engine controls; shared storage contract; fail-closed local writes |
| `2b067f7` | E | feat(documents): chunked upload sessions for serverless-safe large uploads |
| `7d6b100` | F | feat(processing): bounded idempotent processing tick for serverless |
| `7a820de` | H | test(retrieval): prove grounding/isolation parity for the serverless paths |
| `a108354` | I | fix(vercel): serverless entrypoint declares the driver it actually has |
| `e5fcac3` | J | feat(web): chunked upload path and processing tick for serverless |
| `5f69a58` | K | feat(observability): processing-driver metrics and serverless runbook |
| `c3ab950` | — | fix(ci): processing-tick workflow validates and skips cleanly |

Prior Phase-1/2 and ClamAV work (17 migration commits + 5 ClamAV commits)
is carried by the same PR; see `docs/VERCEL_PHASE1_POC.md` and
`docs/PROJECT_COMPLETION_2026-10-31.md`.

**Pull requests:** PR #12 OPEN (arena → main) — dependency order preserved
(ClamAV remediation → migration). PR #13 was auto-marked MERGED by GitHub
on 2026-11-01T17:17:25Z when its base absorbed its head; **no merge to
`main` occurred** (`main` still `9dcbc7c`), nothing was deployed, and the
base cannot be changed on a closed PR. All migration content is on PR #12.
Neither PR was merged or closed by this session.

---

## 5. Requirement-by-requirement classification

### 5.1 Serverless architecture (B, E, F)

- Stateless API, no resident threads on Vercel: **PASS** — code-level
  `start_worker=False` plus forced declared state; regression-tested.
- No duplicate workers: **PASS** — advisory-lock lease (containers) +
  single-driver 409 (tick vs worker); tested both ways.
- 4.5 MB body limit vs 20 MB app limit: **PASS** — chunked sessions;
  every request ≤ `UPLOAD_PART_SIZE_BYTES` (4 MiB default); tested
  (oversize refused mid-stream).
- Direct-to-Blob browser uploads: **BLOCKED (SDK)** — pinned Python SDK
  cannot mint client tokens; API-mediated chunking is the implemented
  equivalent. (Upgrading the SDK when Vercel ships the API is a follow-up.)
- Processing without a resident worker: **PASS** — tick (user + scheduler);
  frontend integration J; scheduled workflow self-skipping until owner
  configures it.
- Retries/DLQ/recovery: **PASS** — attempt-bounded retries; terminal
  `failed` document with `error_code` is the dead-letter record;
  heartbeat-based stale recovery in every tick; tested.

### 5.2 Data layer (C, D)

- Neon-ready pooling/SSL/timeouts: **PASS (code)** — connect timeout,
  recycle, pre-ping, pool bounds; regression-tested hermetically.
- Migrations as a release step: **PASS** — never at cold start; direct
  (non-pooled) endpoint documented; round-trip in CI.
- Real Neon acceptance: **BLOCKED — OWNER ACTION** (provision; see
  `docs/NEON_DATABASE_RUNBOOK.md`).
- Blob adapter (private, opaque keys, no overwrite, bounded streaming,
  error mapping, SDK 0.5.0 compatibility): **PASS** — shared contract +
  failure-path suites.
- Real Blob store smoke: **BLOCKED — OWNER ACTION** (store + token).
- Storage backend interchangeability: **PASS** — same contract suite runs
  against both backends; a real divergence (local overwrite) was found and
  fixed fail-closed (`cf0422e`).

### 5.3 Security (G, H, K)

- ClamAV fail-closed preserved: **PASS (policy)** — including the chunked
  path (EICAR-signature stub test: zero rows, zero objects).
- Real-engine production evidence: **BLOCKED — OWNER ACTION** — ClamAV
  cannot run on Vercel; external scanner host required (single hard
  blocker for production traffic).
- Acceptance tooling isolation: **PASS** — never addresses real production
  hosts; Phase-6 health-gate correction accepted and regression-tested.
- User isolation / IDOR: **PASS** — full matrix plus the new endpoints;
  indistinguishable-404 policy; SQL-predicate ownership.
- Grounded answers / citation integrity: **PASS** — extractive-only,
  verbatim quotes asserted against stored segments; parity on chunked path.
- Prompt-injection defense: **PASS** — neutralizer + delimiters + capped
  untrusted carry-forward + text-only rendering; extractive design means
  injected instructions have nothing to act on.
- Secret hygiene: **PASS** — no storage keys/tokens in any client request
  (tested); scheduler endpoint disabled while token unset; no-fallback
  bearer rule; `.env.example` ships no real secret.
- Correlation IDs, health, metrics: **PASS (code)** — request_id
  everywhere; honest readiness; queue/driver gauges.
- Alert delivery proof: **BLOCKED — OWNER ACTION** (needs real
  deployment).
- Professional pentest: **NOT PERFORMED** (owner follow-up;
  `docs/PENTEST_READINESS.md`).

### 5.4 Product features (H, I, J)

- Document processing/retrieval on serverless: **PASS** (parity proven).
- PPTX generation on serverless: **PASS (serverless-safety)**; PowerPoint
  visual parity: **BLOCKED — OWNER VERIFICATION**.
- Frontend upload flow: **PASS (client layer)** — path selection, tick,
  error parity, no-leak audit. Browser E2E: **NOT AVAILABLE** (no E2E
  suite exists in the repo; listed as owner follow-up).
- Arabic UX: unchanged — all new server messages are Arabic, matching the
  existing envelope.

---

## 6. Owner actions (exact, ordered)

1. **Decide the ClamAV architecture** (hard blocker): external clamd host
   reachable from Vercel Functions, or a scanning sidecar. Then set
   `CLAMD_*` and keep `MALWARE_SCAN_REQUIRED=true`. Evidence needed: real
   infected/clean/outage results from the acceptance tooling against the
   real host.
2. **Provision Neon** (free tier is fine to start) — pooled + direct
   endpoints; follow `docs/NEON_DATABASE_RUNBOOK.md`.
3. **Provision the Vercel Blob store**; set `STORAGE_BACKEND=vercel_blob`
   + `BLOB_READ_WRITE_TOKEN`; then run the real-store smoke (Phase D
   checklist) — no fake-client result counts.
4. **Set production env vars** per `docs/VERCEL_SERVERLESS_RUNBOOK.md` §2
   (incl. new `PROCESSING_TICK_TOKEN`, `METRICS_SCRAPE_TOKEN`).
5. **Run migrations** against the direct endpoint (`alembic upgrade head`).
6. **Configure the scheduled tick**: repo `vars.MAFHUM_API_URL` +
   `secrets.PROCESSING_TICK_TOKEN` (workflow self-skips until then).
7. **Deploy and smoke** per runbook §3 step 7; verify `/health/ready`.
8. **Wire alerting** on the metrics in runbook §5; produce an
   alert-delivery proof.
9. **Real `MAFHUM_API_URL`** for the web deployment + redeploy (currently
   the deliberate `.invalid` placeholder).
10. **Branch protection on `main`**; then merge PR #12 (owner decision).
11. **Follow-ups:** staging DAST against the real deployment; PowerPoint
    parity check; `docs/PENTEST_READINESS.md`; independent verification of
    Phase 1; SDK upgrade when client-token minting ships for Python.

---

## 7. Limitations (explicit)

- Hobby-plan constraints shaped the design (daily cron → GitHub Actions
  5-min scheduler + frontend tick; Fluid compute budget → 25 s tick
  default; no resident processes → no on-Vercel ClamAV).
- The tick's global processing means any authenticated user can drive
  processing of the shared queue — safe (exclusive claims, counters-only
  responses, tested) but worth knowing operationally.
- Chunked upload parts double storage transiently (2× during upload);
  reclaimed at finalize/expiry.
- The Blob adapter pins `vercel-blob==0.5.0`; the SDK surface was verified
  against that exact version.
- PR #13's auto-merge event is GitHub behavior when a base absorbs a head;
  documented here so the history reads honestly.

*Report date: 2026-11-01. Everything verifiable in this report was verified
in this session at the commits listed; nothing unexecuted is reported as
passed.*
