# MAFHUM — Project Architecture

> Brand: **MAFHUM**. Do not call the product “MAFHUM AI” or “DZ University AI” in user-facing surfaces.
> Status: architecture baseline for a new repository. Sections 1–12 below are the
> original Phase 0 plan and are preserved as the design record. **Section 0
> records what has actually been built**, including where reality diverged from
> the plan.

## 0. Implementation status (verified)

Phases 0–3 are implemented and tested. Phases 4–7 are not started. See
[`ROADMAP.md`](ROADMAP.md) for phase detail and
[`THREAT_MODEL.md`](THREAT_MODEL.md) for residual risk.

```
apps/api/    FastAPI application
  src/mafhum_api/
    config.py              pydantic-settings; refuses insecure production config
    core/                  errors, request context, redacted logging, security
    db/                    SQLAlchemy models, session/engine
    services/
      identity/            registration, sessions, scrypt, revocation
      projects/            per-user projects
      documents/
        storage.py         DocumentStorage protocol + LocalFileStorage
        validators.py      magic bytes, MIME, archive shape, limits
        parsers/           pypdf (PDF), python-docx (DOCX)
        normalizer.py      NFC only — never destroys Arabic diacritics
        segmenter.py       paragraph/page-preserving segmentation
        pipeline.py        validate → parse → normalize → segment → persist
        service.py         upload saga with compensating cleanup
        worker.py          bounded pool, FOR UPDATE SKIP LOCKED, stale recovery
      sources/             catalog with provenance, never fabricates
      deletion/            account deletion, resumable, DeletionReport
    api/v1/                health, auth, projects, documents, sources, account
  alembic/                 0001_identity, 0002_documents
  tests/                   435 tests against a real PostgreSQL

apps/web/    Next.js 16 + React 19, TypeScript strict
  src/app/                 Arabic-first RTL, lang="ar" dir="rtl"
  src/lib/                 typed API client, i18n (ar/fr), locale provider
  src/components/          header, alerts, status badge, confirm dialog

tools/e2e/                 live end-to-end verification over real HTTP
```

### Where the implementation diverged from this plan

| Plan said | Reality | Why |
|---|---|---|
| Docling adapter (§7) | pypdf + python-docx | Docling declares 184 dependencies and downloads models at runtime. See [`OSS_REUSE_REGISTER.md`](OSS_REUSE_REGISTER.md) |
| Docker Compose (§10, Phase 1) | Not provided | Docker is unavailable in the verification environment; an untested Compose file would be a claim without evidence |
| Deterministic mock AI provider (§6) | `ENABLE_AI_GATEWAY=false`, no provider code | No Phase 4 retrieval exists to feed it; a mock would risk being mistaken for a real integration |
| Scanned-document benchmark (§10, Phase 3) | Detect and report only | There is no OCR. See [ADR 0003](ADR/0003-no-ocr-no-malware-scanning.md) |

### Operational constraints that are load-bearing

- **One API process.** Quotas, the login throttle and the worker pool are
  process-local. Scaling out requires moving enforcement into the database first.
  See [ADR 0004](ADR/0004-bounded-in-process-worker.md).
- **The browser never learns the API origin.** The frontend issues only relative
  `/api/v1/...` requests, which `next.config.mjs` proxies. Cookies stay
  same-origin, so `SameSite` and the CSRF double-submit work without CORS.
- **PostgreSQL only.** SQLite is not supported; the migrations and
  `SKIP LOCKED` job claiming are PostgreSQL-specific.

## 1. Mission and product constraints
Build a web-first, Arabic-first learning and academic productivity platform for Algerian students, expandable across the Arab world. Arabic UI must use true RTL; French is supported for sources and output. Professional identity: navy and white, accessible contrast, responsive layouts.

MVP is free, with a 0 DZD budget. No payment integration or mandatory paid API. Never promise unlimited or always-on free AI hosting. All AI output is a draft requiring review. Never invent citations, sources, authors, titles, quotations, laws, dates, or page numbers. Do not bypass paywalls, authentication, or source terms. Respect copyright and store only permitted metadata, excerpts, links and user-owned uploads. Prefer suitable open-source reuse after license, dependency, security, maintenance and testing review.

## 2. Primary journeys
1. Visitor understands features, privacy and limitations, then creates an account.
2. Student uploads a PDF/DOCX or chooses an allowed official/academic source; the app validates and quarantines uploads, parses them, reports extraction quality, then indexes permitted content.
3. Student creates research work by specifying topic, specialty, level, language, scope, structure and citation requirements. The system produces an outline first and generates section-by-section from evidence.
4. Each supported claim links to persisted source evidence and, where available, a page or section. Unsupported claims are flagged; no evidence means no fabricated citation.
5. Student creates TD worksheets, a problem statement and plan, or requests a simpler explanation with a different example and a comprehension check.
6. Student creates editable PPTX presentations with Arabic RTL, citations and bibliography.
7. Student saves, renames, exports, resumes and deletes projects and files. Usage quotas and retention rules are explicit.
8. Admin manages connectors, jobs, quotas, feature flags, model configuration and redacted audit events; admin access to user content is restricted.

## 3. Functional modules
- Public website, onboarding, methodology, privacy and academic-use guidance.
- Authentication, sessions, profiles, roles, account deletion and ownership checks.
- Student workspace, projects, documents, saved outputs and usage limits.
- Research studio, source catalog/connectors, retrieval, outline and drafting workflows.
- TD/plan builder and adaptive tutor.
- Document processing pipeline: upload validation, quarantine, parsing/OCR, chunking, provenance and deletion.
- Citation/evidence service with canonical source IDs and claim-to-evidence links.
- Presentation studio for editable PPTX and source notes.
- Provider-neutral AI gateway, deterministic mock provider, optional local/open-weight provider.
- Admin, audit trail, usage accounting, observability and evaluation suite.

## 4. Recommended architecture: modular monolith first
Do not introduce microservices unless measurements justify them. Keep a separable web app and API with a typed API contract.

### Web
- Next.js App Router + TypeScript.
- Tailwind CSS and an accessible headless component library only after license review.
- True RTL at document/layout level, logical CSS properties, locale-aware numbers/dates, mixed RTL/LTR handling for URLs, citations and code. Do not indiscriminately mirror icons/logos.
- Zod or equivalent boundary schemas; server validation is authoritative.

### API
- Python 3.11+; FastAPI; Pydantic v2; API version prefix `/api/v1`.
- Packages/modules: identity, projects, documents, sources, retrieval, research, tutor, presentations, ai_gateway, admin, audit and usage.
- Business logic in services, persistence behind repository interfaces, typed request/response schemas, centralized safe error mapping and request IDs.
- Bounded background jobs for parsing, indexing and PPTX generation. Begin with a database-backed queue or synchronous development adapter; do not require Redis until evidence supports it.
- Timeouts, size/concurrency/token limits, retries with bounds, and cancellation for all expensive operations.

### Persistence and storage
- PostgreSQL for multi-user deployment with migrations.
- SQLite may be used for local development/tests through an adapter, not assumed equivalent under production concurrency.
- Private object-storage abstraction; local private storage for development, S3-compatible provider in deployment. Uploads are never public by default.
- pgvector optional; start with lexical search + metadata and add vectors only when benchmarked retrieval gains justify them. Redis optional.
- Initial entities: users, sessions, projects, documents, document_versions, source_records, source_chunks, research_jobs, research_drafts, claims, claim_evidence, citations, tutor_sessions/messages, presentation_jobs, artifacts, usage_events, audit_events, app_settings and ingestion_jobs.

## 5. Retrieval and Algerian source strategy
Candidate sources include the Algerian Official Journal (JORADP), MESRS, ASJP, SNDL and university repositories. Access and automated-use permissions vary. Build adapters with allowlists and per-domain policies, not unrestricted scraping. Prefer permitted public metadata and catalog APIs. For restricted sources, link out unless indexing is explicitly allowed.

Pipeline: normalize request → query approved source catalog → fetch permitted content → parse → deduplicate → lexical retrieval → optional vector retrieval/reranking only after evaluation → build evidence pack → generate structured draft → validate every citation ID/snippet → save.

Record canonical URL, title, author/issuing body, issue/date, language, document type, access/license notes, retrieval time, parser status, checksum, source version and permitted excerpt bounds. Handle amended/stale law documents. Treat retrieved content as untrusted data; instructions embedded in documents never override system policy. Legal material must link to primary sources and show dates/version when known.

## 6. AI gateway and model selection
Use a provider-neutral interface such as `generate`, `embed`, optional `rerank`, `health` and usage metadata. Feature logic must not hard-code a vendor. Include a deterministic fake provider so tests require no credentials. Local models via an adapter (Ollama/vLLM or equivalent) stay behind an authenticated backend/private network; never expose model runners directly.

Review each exact model card and weights license separately from its repository's code license. Benchmark Modern Standard Arabic, Algerian student language where appropriate, French, mixed Arabic/French, evidence grounding, citation validity, prompt injection, response latency, RAM/VRAM and retrieval quality. Have a degraded mode when no model runtime is available. Add per-user quotas, upload size/page limits, concurrency ceilings, output caps and admin feature switches.

## 7. Open-source reuse register
Record each candidate in `docs/OSS_REUSE_REGISTER.md`: repository, pinned commit/version, exact license and URL, code/modules used, notices, modifications, security review, test status and decision. Prefer dependencies/adapters over copying entire applications; retain upstream notices and add `THIRD_PARTY_NOTICES.md`.

### Candidates inspected preliminarily
- [ScholaRAGent](https://github.com/ShiYu0318/ScholaRAGent) — README and LICENSE inspected; MIT. Candidate reference for academic retrieval, injectable interfaces and deterministic tests. README treats Groq as required for generation in its quick start; remove that assumption and inspect dependencies/security before reusing code.
- [Docling](https://github.com/docling-project/docling) — README, LICENSE and `pyproject.toml` inspected; MIT. Candidate parser for PDFs/DOCX/layout/tables/OCR. Benchmark Arabic/French PDFs and memory/time before enabling expensive OCR.
- [PptxGenJS](https://github.com/gitbrent/PptxGenJS) — LICENSE and `package.json` inspected; MIT, package metadata version 4.0.1 at inspection. Candidate editable PPTX library; test actual Arabic RTL, mixed-script citations, charts and output opening/editability.
- [OpenTutor](https://github.com/zijinz456/OpenTutor) — README, LICENSE and SECURITY.md inspected; MIT. Useful learning-workspace reference. README describes local single-user beta; SECURITY.md requires authentication and a strong JWT secret before public internet exposure. Reference only unless multi-user isolation and security are independently verified.
- [Marp CLI](https://github.com/marp-team/marp-cli) — package metadata inspected; MIT, version 4.5.1 at inspection. Benchmark as an alternative slide workflow; do not assume editable PPTX or Arabic RTL suitability.
- [Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding) — candidate multilingual embeddings/reranking. README describes 100+ languages, but inspect the exact model card and weights license, resource needs and Arabic/French benchmark before adoption.
- [Haystack](https://github.com/deepset-ai/haystack) — optional. Add only if our evaluated retrieval pipeline proves complex enough to justify the framework.

Do not adopt PaperLens community-license code without written permission/legal review. Do not introduce AGPL/GPL applications without compatibility analysis. Do not import unrelated commerce, video, trading or device-sync projects simply because they appear in a list.

## 8. Security and privacy
- Any network deployment requires authentication, strong secret management, secure cookies, appropriate CSRF protection, rate limits, session revocation and brute-force controls.
- Enforce tenant ownership server-side on every project/document/job/artifact operation; never trust client-supplied user IDs or storage paths.
- Uploads: allowlisted file types, MIME+extension verification, limits on bytes/pages, random storage keys, quarantine, malware scan where available, parse in constrained workers, no macro execution, no user-controlled filesystem paths.
- SSRF protection for URL import: block private/reserved/loopback IPs, validate redirects/DNS, restrict schemes/ports, timeout and size.
- Defend against IDOR, path traversal, XSS, SQL injection, CSRF, open redirects, quota bypass, prompt injection and cross-tenant leakage.
- Retrieved documents are untrusted evidence, never tool instructions. Tool calls need server-side authorization and schemas.
- Redact secrets and document content from logs by default; encrypt transport and use managed at-rest encryption when provided.
- Implement export/delete workflows, retention limits, artifact/job cleanup and tests. Admin sensitive actions are audited. Do not claim legal compliance without review.

## 9. API conventions and suggested initial endpoints
- Versioned `/api/v1`, OpenAPI contract, consistent errors `{code, message, request_id, field_errors?}`.
- `GET /health/live`, `GET /health/ready` without leaking secrets.
- `/api/v1/auth/...`, `/api/v1/me`, `/api/v1/projects`, `/api/v1/projects/{id}`.
- `/api/v1/documents`, `/api/v1/documents/{id}`, `/api/v1/documents/{id}/status`.
- `/api/v1/sources/search`, `/api/v1/sources/{id}`.
- `/api/v1/research/jobs`, `/api/v1/jobs/{id}`, `/api/v1/research/{id}`.
- `/api/v1/tutor/sessions`, `/api/v1/tutor/sessions/{id}/messages`, `/api/v1/td/drafts`.
- `/api/v1/presentations/jobs`, `/api/v1/artifacts/{id}/download`, `/api/v1/usage`; admin routes require a distinct role.
These are proposed contracts, not implemented endpoints.

## 10. Phased roadmap and exit gates
### Phase 0 — repository baseline
Create README, `AGENTS.md`, this architecture, roadmap, threat model, data policy, OSS register, ADR, notices, `.gitignore`, `.env.example`, CI. Pin versions, inspect licenses and define commands. No feature code before this gate.

### Phase 1 — runnable skeleton
Next.js Arabic RTL shell, FastAPI API, typed contract, config validation, `/health/live` and `/health/ready`, request ID/safe errors, migrations, Docker Compose local development, deterministic mock AI provider. CI must run frontend lint/typecheck/tests/build and backend lint/typecheck/tests.

### Phase 2 — accounts and project storage
Authentication, sessions/profile, project CRUD, tenant-isolation tests, quotas and account deletion.

### Phase 3 — document ingestion and source catalog
Quarantined upload, strict validation and status reporting; Docling adapter; Arabic/French PDF/DOCX benchmark including scanned files, tables, reading order and deletion propagation.

### Phase 4 — grounded research
Approved source adapters, lexical retrieval, evidence-pack schema, cite resolver, unsupported-claim flags, golden Arabic/French cases. Add embeddings only if benchmark supports it.

### Phase 5 — tutor and TD
Simplification, alternate examples, comprehension questions, TD problem statement/outline. AI output clearly labelled draft.

### Phase 6 — editable presentations
Implemented 2026-10-27 with a `DeckGenerator` protocol. Production uses python-pptx (MIT); PptxGenJS 4.0.1 is measured as a comparison generator on the same six-slide Arabic/French fixture; Marp was rejected as non-editable/experimental. A 6-slide benchmark deck with bullets, two verbatim quotations, a chart, a table, footers and a bibliography is validated for ZIP integrity, native editable objects, paragraph/run/table RTL and mixed-script preservation (`docs/PPTX_BENCHMARK.md`, `docs/ADR/0005-pptx-generator.md`). **Pixel-level RTL rendering in PowerPoint/LibreOffice remains unverified** because neither application is installed in the verification environment.

### Phase 7 — admin, quotas and deployment rehearsal
Roles, abuse controls, redacted logs/metrics, backups and restore drill, data deletion, deployment limits and rollback guide. Confirm free-tier limits from current docs; no promise of free 24/7 GPU inference.

## 11. Quality gates
- Never claim tests/build/security review passed without running commands and presenting actual results.
- Unit + integration + E2E tests for services, permission boundaries, parsers, citation resolver, usage controls, auth, uploads, research and PPTX download.
- Security tests for cross-tenant IDOR, upload validation, prompt injection, SSRF, limits, secrets and dependency audit.
- Research metrics: citation resolution rate, claim support rate, retrieval recall@k, unsupported-claim rate, source freshness and Arabic/French results.
- PPTX: ZIP integrity, editable elements, open/render test, mixed RTL/LTR fixtures.
- Every phase report records commands, exit codes, failures, skipped checks, changed files, reused OSS, license notices, risks and next step.

## 12. Arena execution rules
Read this document first. Verify current repository tree/status before assuming anything. Search GitHub before implementing each capability; inspect README, exact LICENSE, manifests, security policy/advisories, tests and maintenance. Record decisions and preserve notices. Implement one vertical slice at a time with tests and a coherent commit. Never force-push, rewrite history, delete user data or commit secrets. If a credential/runtime is unavailable, provide an interface/mock and document the blocker while continuing locally testable work. Stop for destructive/paid actions only. Never claim deployment succeeded without testing its URL.