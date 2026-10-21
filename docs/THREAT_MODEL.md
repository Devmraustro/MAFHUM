# MAFHUM Threat Model (Phase 2 Security Hardening Review)

## Assets
Student identities/sessions, uploads, research drafts, source records/citations, generated artifacts, AI credentials, usage records, admin settings and audit events.

## Trust boundaries
Browser/web server, reverse proxy/API (`TRUSTED_PROXY_IPS`), web/API (`/api/v1` proxy), API/PostgreSQL database, API/private object storage, API/parser workers, API/model runtime, ingestion workers/public sources, admin/privileged endpoints.

## Threats and implemented Phase 2 controls
- **IDOR / broken authorization**: server-side `owner_id` scoping enforced in every service query (`ProjectService`, `AuthService`); client-supplied `user_id`/`owner_id` fields are ignored/rejected; cross-tenant lookups return uniform `404` without oracle leakage.
- **IP-rotated brute force & password spraying**:
  - Account limiter keys strictly hash `HMAC-SHA256(SESSION_SECRET, NFKC(lower(strip(email))))` — client IP rotation never resets per-account brute-force limits, and zero plaintext email PII is stored in limiter memory (`keys_snapshot()` verified by test).
  - Separate per-IP sliding-window limiter (`AUTH_IP_RATE_LIMIT`) blocks password spraying across distinct accounts from one client IP without blocking unrelated clients.
  - Periodic stale-bucket sweeping bounds memory growth under randomized spray traffic.
- **Spoofed proxy headers (`X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`)**: completely ignored unless the TCP peer address matches configured `TRUSTED_PROXY_IPS` CIDRs; when trusted, `X-Forwarded-For` is scanned strictly right-to-left to select the rightmost untrusted client hop, defeating left-prepended attacker IPs.
- **Concurrent TOCTOU race on quotas & duplicate signups**:
  - Naive `COUNT(*)` checks prior to `INSERT` allow concurrent requests under `READ COMMITTED` to observe the same count (`< quota`) when rows do not yet exist and overshoot `project_quota`.
  - Mitigated in depth by acquiring an exclusive row lock on the parent `users` row (`SELECT ... FROM users WHERE id = :uid FOR UPDATE` on PostgreSQL) *before* counting/mutating, keeping `users.projects_count` synchronized atomically in the same transaction, enforcing `ck_users_projects_within_quota` (`CHECK (projects_count <= project_quota)`) in the database schema, and rolling back failed transactions cleanly.
  - Concurrent duplicate-email `POST /api/v1/auth/register` races catch `IntegrityError`, call `db.rollback()`, and return safe `409 Conflict` envelopes instead of `500` errors.
- **CSRF, login CSRF, cross-origin mutations, and cookie tossing**:
  - All state-changing endpoints validate `Origin`/`Referer` against `ALLOW_DEV_ORIGINS` (including rejecting `Origin: null` and cross-origin login/registration attempts).
  - All cookie-authenticated mutations additionally require a session-bound CSRF token (`HMAC-SHA256(SESSION_SECRET, nonce + session_token)`), rejecting attacker-planted subdomain cookies where `cookie == header` does not match the victim's `HttpOnly` session cookie.
  - Production enables `Secure`, `HttpOnly`, `SameSite=lax`, and `__Host-` cookie prefixes (`__Host-mafhum_session`, `__Host-mafhum_csrf`, requiring `Secure`, `Path=/`, and zero `Domain` attribute).
- **Session lifecycle & key rotation**: server-side sessions store only `HMAC-SHA256(SESSION_SECRET, raw_token)` digests, enforce server-side expiry, support single-session logout, per-device revocation, and revoke-all, and invalidate all active sessions and CSRF tokens immediately upon `SESSION_SECRET` rotation.
- **Secret leakage & header/log injection**: `X-Request-ID` inputs are strictly validated against `^[A-Za-z0-9._-]{1,64}\Z` (preventing CRLF response splitting and log injection); secrets, passwords, raw session tokens, CSRF secrets, and database URLs are excluded from logs and `SafeErrorEnvelope` responses; startup refuses weak secrets, wildcard CORS, `SameSite=none`, missing DBs, or SQLite in production.

## Remaining threats for Phases 3–7 & multi-replica deployments
- **Multi-process / horizontal scaling**: in-memory `SlidingWindowLimiter` bounds per-process attempts; multi-worker / multi-container deployments must move rate-limit counters to shared Redis storage behind the same limiter interface.
- **Malicious uploads / parser exploits (Phase 3)**: MIME+magic-byte allowlist, byte/page caps, quarantine bucket, isolated worker timeouts, no macro execution, and Docling benchmark required before enabling file uploads.
- **SSRF via URL import (Phase 3–4)**: strict domain allowlist, DNS + redirect re-resolution blocking RFC1918/loopback/link-local ranges, scheme/port restrictions, and byte/time limits.
- **Prompt injection & citation fabrication (Phase 4–5)**: untrusted evidence delimiters, server-side citation verification against persisted source spans, and explicit draft disclaimers.
- **Cross-tenant cache/vector/storage leakage & admin audit logging (Phase 4–7)**: mandatory tenant scoping on every future storage/index key plus immutable admin audit logs.

Do not expose model runners, databases, storage or admin endpoints directly. Review this threat model before public deployment and after every phase gate.
