# MAFHUM Roadmap

## Phase 0 — Architecture and repository governance
Exit: architecture, threat model, data policy, OSS reuse register, ADR, README, notices, .gitignore, .env.example and CI committed; stack and test commands documented.

## Phase 1 — Runnable local skeleton
Exit: Next.js TypeScript Arabic RTL frontend; FastAPI API; /health/live and /health/ready; config validation; request IDs and safe errors; migrations; Docker Compose without paid credentials; deterministic mock AI provider. Frontend lint/typecheck/tests/build and backend lint/typecheck/tests have recorded green results.

## Phase 2 — Secure identity and workspace
Exit: authentication, sessions/profile, project CRUD, ownership/tenant-isolation tests, quotas, account export and deletion.

## Phase 3 — Documents and source catalog
Exit: quarantined upload, strict validation, parser status, Docling adapter, Arabic/French PDF/DOCX benchmark including scans/tables/reading order, deletion propagation and source provenance.

## Phase 4 — Grounded research
Exit: approved source adapters, lexical retrieval baseline, evidence pack, citation resolver, unsupported-claim warnings, golden Arabic/French evaluations.

## Phase 5 — Tutor and TD tools
Exit: simpler explanations, alternate examples, comprehension checks, TD/problem-statement/outline drafts and safe prompt construction tests.

## Phase 6 — Editable presentations
Exit: PptxGenJS benchmark and optional Marp comparison; six-slide Arabic/French deck with bullets, quote, chart, table and bibliography; verified PPTX integrity/editability and inspected RTL render.

## Phase 7 — Admin and deployment rehearsal
Exit: distinct admin permissions, usage limits, audit events, redacted logs, backup/restore and deletion tests, actual deployment/cold-start/resource constraints and rollback guide.

### Change control
No phase advances without evidence. Record actual commands and exit codes. Do not claim deployment/provider operation without testing in the target environment.
