# MAFHUM Roadmap

Status is recorded from verification, not intent. "Done" means implemented **and**
covered by a test that actually ran; see [`docs/THREAT_MODEL.md`](THREAT_MODEL.md)
for what remains a residual risk.

**Current status (2026-10-28):** the review branch adds production ClamAV wiring
with real engine acceptance, bounded account export, configurable audit retention,
an encryption design (interfaces only, NOT enabled), a scoped metrics token with
Prometheus/alert templates, a security CI gate, a headless LibreOffice render
gate, Windows URL portability, doc corrections and a Compose contract validator.
`docker compose up` HAS been executed on CI (rehearsal `40530008866` and later
`40593579965`); the *production* blueprint has only been config-validated. There
is still **no public API, no production deployment, no delivered alerting and no
penetration test**. The project is not complete.

| Phase | Status |
|---|---|
| 0 — Architecture and governance | Done |
| 1 — Runnable local skeleton | Done |
| 2 — Secure identity and workspace | Done |
| 3 — Documents and source catalog | Done, with documented limitations |
| 4 — Grounded research | Done, with documented limitations |
| 5 — Tutor and TD tools | Done, with documented limitations |
| 6 — Editable presentations | Done, with documented limitations (no visual render check) |
| 7 — Admin and deployment rehearsal | Implemented with documented limits — see below (still no production deployment) |

---

## Phase 0 — Architecture and repository governance

**Exit:** architecture, threat model, data policy, OSS reuse register, ADR,
README, notices, `.gitignore`, `.env.example` and CI committed; stack and test
commands documented.

Done. `.env.example` is now covered by a contract test that fails if it documents
a variable `Settings` does not read.

## Phase 1 — Runnable local skeleton

**Exit:** Next.js TypeScript Arabic RTL frontend; FastAPI API; `/health/live` and
`/health/ready`; config validation; request IDs and safe errors; migrations;
deterministic mock AI provider.

Done, except: the AI gateway is a disabled flag rather than a mock provider.
A root `docker-compose.yml` and `apps/api/Dockerfile` are now tracked, and Gate B
verified the API image build, non-root runtime, readiness and graceful shutdown
on a GitHub-hosted runner (run `40377229156`). **`docker compose up` on the full
stack has not been executed**, and there is no `apps/web/Dockerfile`; neither is
claimed as verified.

## Phase 2 — Secure identity and workspace

**Exit:** authentication, sessions, project CRUD, ownership/tenant-isolation
tests, quotas, account export and deletion.

Done. Login throttling and quotas were hardened to be **database-enforced**
(`login_attempts` table, `SELECT ... FOR UPDATE` on the owning user row) and
are verified across two independent application instances. The document
processing worker remains per-process — see `docs/DEPLOYMENT.md`.
Account **export** is not implemented — deletion is.

## Phase 3 — Documents and source catalog

**Exit (as originally written):** quarantined upload, strict validation, parser
status, Docling adapter, Arabic/French PDF/DOCX benchmark including scans,
tables and reading order, deletion propagation and source provenance.

**Done:** private (quarantined) upload, strict validation, parser status,
Arabic and French PDF/DOCX extraction, deletion propagation, source provenance.

**Deliberately changed:**

- **No Docling adapter.** Docling was evaluated and rejected: its slim
  distribution declares 184 dependencies and the pipeline downloads models at
  runtime. MAFHUM uses pypdf (BSD-3-Clause) and python-docx (MIT) instead —
  one parser per format. See
  [`docs/OSS_REUSE_REGISTER.md`](OSS_REUSE_REGISTER.md).
- **No scanned-document or table benchmark, because there is no OCR.** Scanned
  PDFs are *detected and reported*; no text is extracted. Claiming a benchmark
  for a capability that does not exist would be false.

**Known limitations carried forward:** no OCR, no URL ingestion, the processing
worker pool is per-process (one worker per database, enforced by an advisory
lock) while quotas and the login throttle are database-enforced, and mixed
Arabic/Latin lines may not round-trip through PDF text extraction. Malware
scanning has an engine integration (`clamd` over its socket protocol) and Gate A
verified detection in the acceptance environment, but no engine ships with the
product — see `docs/SECURITY_ASSESSMENTS.md`.

## Phase 4 — Grounded research

**Exit:** approved source adapters, lexical retrieval baseline, evidence pack,
citation resolver, unsupported-claim warnings, golden Arabic/French evaluations.

**Done, with documented limitations.** Answers are **selections from real
`DocumentTextSegment` rows, never generations** — there is no language model on
the grounded path, so every citation is a verbatim quotation carrying the
document, page and ordinal it came from. Retrieval is lexical and
language-aware (Arabic diacritics and alef/ya/ta-marbuta folded before
matching); "insufficient evidence" is a first-class answer. External source
retrieval (JORADP/ASJP/SNDL) is opt-in (`RESEARCH_RETRIEVAL_ENABLED=false` by
default) and SSRF-reviewed — see `docs/SSRF_REVIEW.md`. Golden Arabic/French
grounding evaluations run in CI (`tests/test_grounding_evaluation.py`,
`tools/evaluate_citation_grounding.py`, `docs/GROUNDING_EVALUATION.md`).

**Limitations:** lexical matching, not semantic — a paraphrase that shares no
content words finds nothing, and the engine says so rather than guessing. No
paid provider is required and none is wired up; `ENABLE_AI_GATEWAY` remains a
disabled flag.

## Phase 5 — Tutor and TD tools

**Exit:** study workspaces over grounded material; question generation with
feedback; flashcards with spaced repetition; study plans.

**Done, with documented limitations.** Topics attach documents (and, once
retrieval is enabled, sources); every artifact stores its provenance and a
deleted source is reported as unavailable rather than silently dropped
(`SET NULL`, not `CASCADE`). Grounding reuses the Phase 4 engine over real
segments — there is no second retrieval path. Question feedback is a lexical
comparison that reports itself as such (`graded_semantically` is always
`false`). Flashcards schedule with the published SM-2 algorithm
(`services/study/scheduling.py`), tested at its boundaries; no learning-outcome
claim is made. Saved research keeps its citations and re-derives availability
on read.

## Phase 6 — Editable presentations

**Done, with documented limitations.** A `DeckGenerator` protocol with a
`python-pptx` production adapter writes native editable text boxes, a real chart
and a real table; Arabic direction is written at paragraph/run/table level.
PptxGenJS 4.0.1 was evaluated against the **same six-slide Arabic/French
`DeckSpec` fixture** and kept as a comparison tool (Marp was rejected). Measured
results are in `docs/PPTX_BENCHMARK.md`, the decision in
`docs/ADR/0005-pptx-generator.md`.

Endpoints: `POST/GET /api/v1/presentations/jobs`, `GET/DELETE
/api/v1/presentations/jobs/{id}`, `GET /api/v1/artifacts/{id}/download`.
Artifacts are private blobs with ownership checked in SQL; account deletion
erases them before dropping the rows. Generation is synchronous and bounded by
caps on evidence, slides, text and output size (see ADR 0005).

**Limitations:** no LibreOffice/PowerPoint render was available here, so visual
fidelity is unverified; fonts are not embedded; generation is not a background
job; a deck built from a deleted source keeps its snapshot but reports
`sources_available=false`.

## Phase 7 — Admin and deployment rehearsal

**Implemented with documented limits.** Migration `0009_admin_audit` adds a
database CHECK on `users.role IN ('student','admin')` and an append-only
`audit_events` table. Roles are granted **only** by the operator CLI
`apps/api/tools/set_admin.py` — there is deliberately no HTTP role-change
endpoint. Denied admin access, registrations, logins, failed logins, logouts,
account/document/presentation deletion and creation, and admin views all write
redacted audit rows; `actor_id` uses `ON DELETE SET NULL` so account deletion
keeps the trail.

Read-only admin API: `/api/v1/admin/overview`, `/usage`, `/audit-events`,
`/metrics` (redacted Prometheus-style counters, no PII). Tests cover the role
gate, the 404-for-non-admin behaviour, the absence of write endpoints, the DB
CHECK, PII hygiene and trail survival.

Deployment artefacts: `apps/web/Dockerfile` (multi-stage, non-root, prod deps
only), `apps/web/.dockerignore`, `apps/web/vercel.json`, a `web` service in
`docker-compose.yml`, and `docs/DEPLOYMENT_RUNBOOK.md` +
`docs/ROLLBACK_GUIDE.md`. `.github/workflows/deployment-rehearsal.yml` builds
the full three-service stack on a Docker runner, exercises it over real HTTP
(`tools/e2e/compose_smoke.py`) and performs a **quiesced backup + restore into
a scratch database** with checksum verification.

**Rehearsal evidence (2026-10-27):** `deployment-rehearsal.yml` run
`40530008866` — full Compose stack built/started, real HTTP journey, quiesced
backup+restore drill, **success**. Staging gates A/B/C re-ran on `main` in run
`40523074856` — success. A Vercel web deployment
(`dpl_Hhpr51CPn24fZT91yisV78S4i6oy`, https://mafhum-web.vercel.app/) serves the
Arabic RTL page over real HTTP but **cannot reach any API** because the API is
not hosted there — a frontend-only rehearsal. See
`docs/DEPLOYMENT_REHEARSAL_REPORT.md`.

**Post-merge follow-up (2026-10-27):** PR #3 merged into `main` and its push
automatically re-ran CI, the staging gates and the deployment rehearsal on
`main`. Five documentation contradictions found in review were corrected,
`admin_access_denied` writes are now throttled to one row per actor per minute,
`admin_metrics_viewed` is audited, and the operator CLI refuses to demote the
last admin without `--force`. A free scheduled uptime probe
(`.github/workflows/uptime-monitor.yml`, `tools/monitoring/probe.py`) and a
structural PPTX pre-render gate (`apps/api/tools/check_pptx_visual.py`) were
added, plus a self-hosted production blueprint in `deploy/`.
See `docs/MONITORING.md`, `docs/FULL_STACK_DEPLOYMENT.md` and
`docs/STATUS_REPORT_2026-10-27.md` §13.

**Still open:** no public API origin (the Vercel deployment is deliberately
frontend-only, and the user chose to keep it that way); no managed PostgreSQL,
TLS terminator, secrets manager, private object store or volume encryption; no
**deployed** monitoring stack and no alert destination (a scoped token,
`prometheus.yml`, alert rules and an optional monitoring Compose file now exist
as templates); no Microsoft PowerPoint visual-parity check on Windows (a
headless LibreOffice CI render now exists); no production encryption at rest
(ADR 0006 is design-only); no DAST/pentest; no OCR, AI provider or live-source
retrieval; no `LICENSE` (owner decision recorded as "not now"). The
`deploy/` stack is configuration, not an executed deployment.

---

## Change control

No phase advances without evidence. Record the actual command and exit code.

- Do not claim a deployment, provider, or external integration works without
  testing it in the target environment.
- Do not mark a feature complete because its page or endpoint exists.
- Do not disable a test or a security control to obtain a green build.
- Do not document a capability that has not been verified — record the
  limitation instead.
