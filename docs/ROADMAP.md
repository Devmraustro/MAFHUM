# MAFHUM Roadmap

## Phase 0 — Architecture and repository governance (Completed)
Exit: architecture, threat model, data policy, OSS reuse register, ADR, README, notices, `.gitignore`, `.env.example` and CI committed; stack and test commands documented.

## Phase 1 — Runnable local skeleton (Completed)
Exit: Next.js TypeScript Arabic RTL frontend; FastAPI API; `/health/live` and `/health/ready`; config validation; request IDs and safe errors; migrations (`0001_initial`); Docker Compose without paid credentials; deterministic mock AI provider. Frontend and backend quality gates verified green.

## Phase 2 — Secure identity and workspace + Security Hardening Gate (Completed)
Exit:
- Server-validated registration, login, logout, per-session listing/revocation, revoke-all, session expiration, and `SESSION_SECRET` rotation (`HttpOnly` cookies, `Secure` + `__Host-` prefixes in production, `HMAC-SHA256` session digests, `Origin`/`Referer` verification, and session-bound `HMAC-SHA256` CSRF protection across every state-changing endpoint).
- Dual independent auth rate limiters: IP-independent `HMAC-SHA256` account limiter (zero raw email PII in memory; resists IP rotation) + per-IP limiter (resists password spraying), with right-to-left `X-Forwarded-For` parsing strictly restricted to `TRUSTED_PROXY_IPS` CIDRs.
- User-owned projects CRUD (`GET/POST /api/v1/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}`) with query-level `owner_id` isolation, uniform `404` policy, and concurrency-safe quotas (`SELECT ... FOR UPDATE` on parent `users` row in PostgreSQL + `ck_users_projects_within_quota` check constraint in migration `0003_quota_check_constraint`, verified under simultaneous multi-request tests on both SQLite and live PostgreSQL).
- JSON account export (`GET /api/v1/me/export`) and atomic account deletion (`DELETE /api/v1/me`) with honest backup/log retention disclosures.
- Real PostgreSQL migration (`upgrade` + `downgrade` + data preservation), constraint, cascade, session, and concurrency tests verified alongside `pip-audit` (0 vulnerabilities) and `npm audit --omit=dev` (0 vulnerabilities).

## Phase 3 — Documents and source catalog (Next — Do not start until requested)
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
