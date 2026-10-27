# MAFHUM Roadmap

Status is recorded from verification, not intent. "Done" means implemented **and**
covered by a test that actually ran; see [`docs/THREAT_MODEL.md`](THREAT_MODEL.md)
for what remains a residual risk.

| Phase | Status |
|---|---|
| 0 — Architecture and governance | Done |
| 1 — Runnable local skeleton | Done |
| 2 — Secure identity and workspace | Done |
| 3 — Documents and source catalog | Done, with documented limitations |
| 4 — Grounded research | Done, with documented limitations |
| 5 — Tutor and TD tools | Done, with documented limitations |
| 6 — Editable presentations | Not started |
| 7 — Admin and deployment rehearsal | Partially done — backup/restore tooling implemented; no admin role, no deployment rehearsal |

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

**Not started.** PptxGenJS has not been evaluated and no generator exists.

## Phase 7 — Admin and deployment rehearsal

**Partially done.** Backup and restore tooling exists and is tested
(`apps/api/tools/backup.py`, `tests/test_backup_restore.py`). Container build,
non-root runtime, readiness and graceful shutdown are exercised by the staging
acceptance workflow (`.github/workflows/staging-acceptance.yml`, Gate B) —
those gates are the deployment rehearsal evidence. Still not started: an admin
role and audit events. No deployment has been performed on real infrastructure.

---

## Change control

No phase advances without evidence. Record the actual command and exit code.

- Do not claim a deployment, provider, or external integration works without
  testing it in the target environment.
- Do not mark a feature complete because its page or endpoint exists.
- Do not disable a test or a security control to obtain a green build.
- Do not document a capability that has not been verified — record the
  limitation instead.
