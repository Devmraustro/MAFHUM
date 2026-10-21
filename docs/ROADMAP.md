# MAFHUM Roadmap

## Phase 0 — Architecture and repository governance (Completed)
Exit: architecture, threat model, data policy, OSS reuse register, ADR, README, notices, `.gitignore`, `.env.example` and CI committed; stack and test commands documented.

## Phase 1 — Runnable local skeleton (Completed)
Exit: Next.js TypeScript Arabic RTL frontend; FastAPI API; `/health/live` and `/health/ready`; config validation; request IDs and safe errors; migrations (`0001_initial`); Docker Compose without paid credentials; deterministic mock AI provider. Frontend and backend quality gates verified green.

## Phase 2 — Secure identity and workspace (Completed)
Exit:
- Server-validated registration, login, logout, per-session listing/revocation, revoke-all, and session expiration (`HttpOnly` cookies, keyed `HMAC-SHA256` server session digests, double-submit `X-CSRF-Token` protection, sliding-window auth rate limiting, startup production secret/DB checks).
- Minimal user profile (`GET/PATCH /api/v1/me`) without privilege-escalation surface.
- User-owned projects CRUD (`GET/POST /api/v1/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}`) with strict query-level `owner_id` isolation, consistent `404` policy, pagination, timestamps, and SQL check constraints (`0002_identity_and_projects`, non-destructive upgrade verified).
- Configurable server-enforced project quotas (`GET /api/v1/me/usage`), JSON account export (`GET /api/v1/me/export`), and CSRF-protected account deletion (`DELETE /api/v1/me`) with honest backup/log retention disclosures.
- Dedicated security test suite + `pip-audit` (0 vulnerabilities) + `npm audit --omit=dev` (0 vulnerabilities) verified green.

## Phase 3 — Documents and source catalog (Next)
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
