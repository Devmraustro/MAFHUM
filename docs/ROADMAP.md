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
| 4 — Grounded research | Not started |
| 5 — Tutor and TD tools | Not started |
| 6 — Editable presentations | Not started |
| 7 — Admin and deployment rehearsal | Not started |

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

Done, except: **Docker Compose is not provided** and the AI gateway is a disabled
flag rather than a mock provider. Docker is unavailable in the verification
environment, so a Compose file could not be built or tested; shipping an
unverified one would violate the change-control rule below.

## Phase 2 — Secure identity and workspace

**Exit:** authentication, sessions, project CRUD, ownership/tenant-isolation
tests, quotas, account export and deletion.

Done. Login rate limiting is **process-local** and documented as such.
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

**Known limitations carried forward:** no OCR, no malware scanning, no URL
ingestion, quotas and worker are process-local, and mixed Arabic/Latin lines may
not round-trip through PDF text extraction.

## Phase 4 — Grounded research

**Exit:** approved source adapters, lexical retrieval baseline, evidence pack,
citation resolver, unsupported-claim warnings, golden Arabic/French evaluations.

**Not started.** `ENABLE_AI_GATEWAY` exists and defaults to `false`. No source
adapter, retrieval or citation code exists. No paid provider is required and none
is wired up.

## Phase 5 — Tutor and TD tools

**Not started.** This must not be built on mock data; it depends on Phase 4
retrieval so that explanations can be tied to real source passages.

## Phase 6 — Editable presentations

**Not started.** PptxGenJS has not been evaluated and no generator exists.

## Phase 7 — Admin and deployment rehearsal

**Not started.** No admin role, audit events, backup/restore tooling, or
deployment rehearsal. `assert_production_ready()` guards the most dangerous
misconfigurations, but no deployment has been performed or verified.

---

## Change control

No phase advances without evidence. Record the actual command and exit code.

- Do not claim a deployment, provider, or external integration works without
  testing it in the target environment.
- Do not mark a feature complete because its page or endpoint exists.
- Do not disable a test or a security control to obtain a green build.
- Do not document a capability that has not been verified — record the
  limitation instead.
