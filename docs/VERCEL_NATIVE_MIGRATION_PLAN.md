# MAFHUM — Vercel-Native Migration Plan

**Status:** Phase 1 implemented — FastAPI deployment proof on `feat/vercel-native-migration` (see `docs/VERCEL_PHASE1_POC.md`), CI green on PR #13 and an isolated `mafhum-api-poc` preview deployment built READY from that commit; awaiting independent verification before Phase 2.  
**Branch:** `feat/vercel-native-migration`  
**Baseline:** PR #12 head `11ccaed3fb5bdf706494a2f04937299969d8ba10`  
**PR #12 remains open and unmerged.**  
**Vercel team plan observed during audit:** Hobby. Re-check entitlements before selecting optional platform products.

## Goals and non-goals

- Move MAFHUM runtime and managed dependencies into a Vercel-centered deployment architecture.
- Preserve authentication, authorization, CSRF protections, document privacy, malware fail-closed behavior, idempotency, auditability, and data integrity.
- Keep all work isolated from `main`, the existing `mafhum-web` production configuration, and unrelated projects.
- Do not create paid resources, upgrade plans, purchase add-ons, change production environment variables, promote deployments, or migrate real user data without explicit owner approval.
- This plan is not a claim that Vercel Hobby supports every required capability.

## Current architecture constraints found in the repository

1. The web app is Next.js and currently proxies same-origin `/api/*` and `/health/*` to `MAFHUM_API_URL`.
2. The API is FastAPI with PostgreSQL-specific SQLAlchemy/Alembic migrations.
3. The current document storage adapter uses persistent local filesystem paths; Vercel Function filesystems must not be treated as durable storage.
4. The current processing worker/dispatcher runs inside the API process. It must not be assumed to remain alive across serverless invocations or cold starts.
5. Malware scanning uses the ClamAV daemon protocol (TCP or Unix socket). A Python package or mocked scanner is not a production malware engine.
6. Document extraction and PPTX generation are potentially long-running operations and need durable job orchestration.
7. Current upload limit is larger than the Vercel Function request-body limit; large files must not be proxied through a Function body.
8. The production runbook assumes Docker Compose and a real ClamAV daemon. That deployment model cannot be copied directly into a standard Vercel Function.

## Proposed target components (subject to compatibility proof)

| Concern | Candidate | Required proof |
|---|---|---|
| Web | Existing Next.js project on Vercel | Preview build and route smoke tests |
| API | FastAPI on Vercel Python Runtime | Cold-start, routing, lifespan, dependency, cookie/CSRF and DB tests |
| Database | Managed PostgreSQL integration (e.g. Neon) | Connection pooling, TLS, migration safety, backups, region/latency, pricing |
| Files | Private Vercel Blob | Authenticated upload/download, size, multipart, deletion, retention and ownership tests |
| Jobs | Vercel Queues and/or Workflows | Delivery semantics, retry, visibility/lease, idempotency, dead-letter/recovery, limits and plan entitlement |
| Worker runtime | Daemon only if enabled for this team/plan; otherwise supported external compute is required | Persistent execution, resource sizing, restart behavior and cost |
| Malware scanning | Real ClamAV-capable service/runtime | EICAR acceptance, scanner health gate, fail-closed behavior, timeout and outage tests |

## Mandatory architecture decisions

### API and database
- Keep PostgreSQL as the source of truth.
- Never use SQLite as a production fallback.
- Configure a single supported region close to the database for Hobby, after checking availability.
- Use bounded connection pools appropriate to serverless concurrency; do not create an unbounded pool per invocation.
- Run Alembic migrations as a controlled release step, not automatically from every Function cold start.

### Upload and private file handling
- Use a direct-to-Blob or presigned upload flow so file bytes do not transit through the Function request-body limit.
- Issue upload authorization only after authenticating the user and creating a server-side upload intent bound to the user, document ID, maximum size, content type policy, and expiry.
- Treat the uploaded blob as quarantined/untrusted until server-side metadata, ownership, size, digest, and malware scan checks pass.
- Keep Blob private. Do not return raw public URLs for private documents.
- Store only Blob pathname/object identifiers and verified metadata in PostgreSQL.
- Downloads must re-check tenant/user authorization and stream/fetch the private object server-side.
- Add cleanup for abandoned uploads and orphaned objects.

### Jobs and document processing
- Replace in-process background threads with durable jobs.
- Use at-least-once delivery safely: each job must be idempotent and guarded by DB state/lease or unique operation key.
- Persist job progress and failure state in PostgreSQL; do not rely on process memory.
- Acknowledge queue messages only after durable state transitions are committed.
- Implement bounded retries, backoff, poison-message handling, cancellation and operator recovery.
- Separate lightweight request validation from extraction, indexing and PPTX generation.
- Never silently drop a job if the consumer is unavailable.

### Malware scanning
- Preserve fail-closed semantics: a file cannot become available for processing/download until a real scanner reports a clean result.
- If scanner health is unavailable, timeouts, malformed responses or engine errors occur, keep the file quarantined and fail/retry safely.
- Do not replace ClamAV with extension/MIME checks, signatures-only mocks, or an always-clean stub.
- Do not claim production malware scanning until an isolated real-engine EICAR test and deployed health/acceptance test both pass.
- If no compatible Vercel-supported daemon/runtime is available under the current plan, record a blocker. Do not weaken the security model to force a Vercel-only claim.

## Phased execution and acceptance gates

### Phase 0 — branch and platform inventory
- Use this branch only; leave `main` and PR #12 untouched.
- Verify actual Vercel plan entitlements for Python Functions, Queues, Workflows, Daemons, Blob, and database integration.
- Record region, duration, memory, payload, storage, concurrency and cost limits with official source links.
- No paid upgrades or resource creation.

### Phase 1 — FastAPI deployment proof
- Add the smallest supported Vercel FastAPI entrypoint and configuration on this branch.
- Do not remove the existing deployment path or alter the current production project's environment variables.
- Verify health route, API route, cookies, CSRF, auth, startup/shutdown behavior and DB connectivity using isolated preview resources only.
- Build and run the existing tests; add deployment-specific regression tests.

### Phase 2 — private Blob adapter
- Implement a storage interface preserving the current API contract.
- Add a Vercel Blob adapter and test private access, ownership, signed/presigned uploads, size enforcement, digest verification and cleanup.
- Keep the local adapter for tests/development.
- No production file migration yet.

### Phase 3 — durable processing
- Implement queue/workflow adapter behind an interface.
- Add durable idempotent job lifecycle and retries.
- Prove document extraction and PPTX generation with representative files, including timeout/retry/restart scenarios.
- Preserve existing API status semantics.

### Phase 4 — malware scanning gate
- Prove a real ClamAV service can be operated in the intended deployment and is permitted by the selected plan.
- Run EICAR positive detection and clean-file negative tests.
- Test scanner-down and timeout fail-closed paths.
- If impossible within the current Vercel plan, stop and report the exact blocker and compliant alternatives.

### Phase 5 — integration and release readiness
- Run full backend/frontend/security/migration suites.
- Run isolated end-to-end upload → quarantine → scan → extraction → indexing → answer → PPTX download.
- Validate backup/restore and data migration/rollback procedures.
- Provide a cost estimate and an explicit list of required plan upgrades/integrations.
- Production release requires owner approval after evidence review.

## Stop conditions

Stop without weakening security if any of the following cannot be proven:
- Private document storage and per-user authorization.
- Durable job delivery and recoverable processing.
- Real malware scanning with fail-closed behavior.
- Database migration and backup/restore safety.
- Required compute/queue/storage features are not available under the selected plan.

## Evidence discipline

Report separately:
- Code-level tests run locally/CI.
- Vercel preview deployment evidence.
- External service integration evidence.
- Real malware-engine acceptance evidence.
- Production-host acceptance evidence.

A green unit-test suite or successful frontend deployment is not proof of complete production readiness.
