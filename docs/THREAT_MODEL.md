# MAFHUM Threat Model (Initial)

## Assets
Student identities/sessions, uploads, research drafts, source records/citations, generated artifacts, AI credentials, usage records, admin settings and audit events.

## Trust boundaries
Browser/web server, web/API, API/database, API/private object storage, API/parser workers, API/model runtime, ingestion workers/public sources, admin/privileged endpoints.

## Threats and required controls
- IDOR/broken authorization: enforce ownership in every service/repository query; test user A cannot read/change/delete user B's items.
- Malicious uploads/parser exploits: MIME+extension allowlist, byte/page limits, quarantine, patched parsers, isolated workers, timeouts, no macro execution and cleanup.
- SSRF via URL import: allowlist sources, block loopback/private/reserved IPs, validate DNS and redirects, restrict schemes/ports, enforce byte/time limits.
- Prompt injection inside documents/pages: treat retrieved content as untrusted evidence, never as system instructions; validate tools server-side.
- Citation fabrication: cite only persisted source IDs/snippets; validate citations; flag unsupported claims.
- Secret leakage: environment secrets only; scan repository; redact logs; rotate exposed keys.
- Session abuse: strong secrets, secure cookies, expiry/revocation, rate limits, brute-force and CSRF controls as appropriate.
- Cross-tenant leakage via vector search/cache/storage: tenant scope on search, cache keys and object metadata; cross-tenant tests.
- Resource exhaustion: input/page/output limits, bounded concurrency, timeouts, quotas and cancellation.
- XSS/injection: output encoding, safe Markdown rendering, parameterized queries, CSP where suitable.
- Admin abuse: least privilege and audit every sensitive action.
- Data surviving deletion: lifecycle tests for originals, text, chunks, embeddings, artifacts and caches.
- Supply-chain issues: lockfiles, reviewed upstream provenance, dependency audit and pinned releases.

Do not expose model runners, databases, storage or admin endpoints directly. Never log full documents/secrets by default. Review this threat model before public deployment and after major architecture changes.
