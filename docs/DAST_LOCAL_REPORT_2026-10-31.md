# Local authenticated DAST — execution report (2026-10-31)

Status: **EXECUTED.** 86 checks passed, 0 failed, at commit
`17d6313dd9fada2629b0997aa995141691282e3a` (branch
`arena/37adf787-mafhum`). This is the first executed DAST in the project's
history; previous phases prepared the procedure but did not run it.

## 1. Authorization record (docs/PENTEST_READINESS.md §3)

| Item | Value |
|---|---|
| Host | `127.0.0.1` inside an ephemeral, single-tenant sandbox — the stack the tool boots itself (`.dast/`, gitignored). No public origin, no third-party service. |
| Time window | 2026-10-31, this working session (single run of ~41 s per execution; three executions during development, final evidence run at the commit above) |
| Tester | Arena agent session on branch `arena/37adf787-mafhum`, acting on the owner's written instruction in this session to complete the project ("أكمل إنجاز المشروع… كاملاً") |
| Data | Synthetic accounts (`@example.com`) and synthetic PDF/DOCX files only; EICAR test string only; no real personal data, no real malware |
| Rate/impact limits | Login throttle verified to the documented limit (10 failures / 60 s) and then **stopped**; no load tests, no DoS, no brute-forcing beyond the throttle |

## 2. Target and tooling

| Component | Version / detail |
|---|---|
| API under test | `mafhum_api` at `17d6313`, two uvicorn instances |
| Instance A | `https://127.0.0.1:8443` — `APP_ENV=production`, `COOKIE_SECURE=true`, `MALWARE_SCAN_REQUIRED=true`, clamd **protocol stub** reachable on 127.0.0.1:3311, processing worker on |
| Instance B | `https://127.0.0.1:8444` — same production posture, engine unreachable (`scan_error` path), worker off (documented replica mode) |
| Database | Real PostgreSQL 16.2 via pgserver 0.1.4, real `alembic upgrade head` |
| TLS | Self-signed ECDSA P-256 certificate (cryptography 50.0.2), so `Secure` cookies are exercised for real |
| Scan client | httpx 0.28.1 (cookie jars per synthetic user), Python 3.11.2 |
| Framework versions | FastAPI 0.159.1, SQLAlchemy 2.1.6, psycopg 3.3.6, uvicorn 0.54.0 |
| Reproduction | `.venv/bin/python tools/dast/local_dast.py [--keep]` (exit 0 = all checks pass); also runs as the `Local authenticated DAST (production posture)` job in `ci.yml` on every commit |

## 3. Scope executed (PENTEST_READINESS §4)

| Area | Result | Notes |
|---|---|---|
| Authentication (register/login/logout/session) | **10/10 PASS** | incl. duplicate email 409, wrong password 401, post-logout invalidation |
| Session + CSRF | **9/9 PASS** | `mafhum_session` is HttpOnly + Secure + SameSite=Lax; CSRF cookie is script-readable by design; missing/foreign Origin, missing and forged token all rejected with `422 csrf_failed` |
| Authorisation isolation | **11/11 PASS** | cross-user read/download/text/delete all 404; not-owned vs missing responses identical modulo `request_id`; listings contain only the caller's rows; unauthenticated reads 401 |
| Upload validation | **7/7 PASS** | PDF-as-DOCX, wrong MIME, oversized, ZIP bomb all 422; traversal filename `../../evil.pdf` accepted but **neutralized** (`evil.pdf`) and no file escaped the storage root |
| Malware policy | **8/8 PASS** | clean upload accepted via stub CLEAN; EICAR DOCX rejected `malware_detected`; engine outage rejected `scan_error` (never CLEAN, never `malware_detected`); stub provably received the bytes (6 scans); `/documents/limits` discloses `malware_scanning_available=false`; `/health/ready` reports `degraded` + `malware_scanner=unavailable` on outage, `ok` with the engine |
| Admin + audit | **5/5 PASS** | unauthenticated 401; non-admin gets 404 (indistinguishable from a missing route); admin role granted via CLI only (`tools/set_admin.py`, audited) |
| Scoped metrics | **6/6 PASS** | valid bearer reads metrics; wrong bearer 401; token grants nothing outside `/admin/metrics`; admin session path works; wrong bearer with admin session fails loudly (no fallback); all documented metric families present |
| Data export | **6/6 PASS** | valid ZIP; no `../` or absolute entries; manifest lists exactly the caller's 4 documents and `files/` carries exactly those 4 blobs |
| Deletion | **6/6 PASS** | `DELETE /api/v1/me` → `complete=true`, `documents_removed=1`, `blobs_removed=1`; blobs gone from disk; session invalid; **audit events survive with NULL actor** (6 events) |
| Error hygiene | **7/7 PASS** | malformed JSON, hostile UUID, unknown route, wrong method — all clean envelopes, no traceback/SQL/paths |
| Login throttle | **1/1 PASS** | `codes=[401×6, 429]` — engages within the documented 10/60 s limit, then the scan **stopped** |
| TLS/headers | **5/5 PASS** | API over TLS; `/api/docs` disabled in production; Caddyfile HSTS + XFO/nosniff/Referrer-Policy + **CSP (remediated, see F-1)** |

## 4. Findings

### F-1 (low, remediated): no Content-Security-Policy in the transport template

- **Title:** `deploy/Caddyfile` set HSTS, `X-Content-Type-Options`,
  `X-Frame-Options: DENY` and `Referrer-Policy` but no
  `Content-Security-Policy`; no CSP exists in the Next.js app either.
- **Severity:** Low (defense-in-depth gap; framing and MIME confusion were
  already mitigated by XFO/nosniff).
- **CVSS v3.1 vector (pre-remediation):** AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:L/A:N
  (score 4.2, medium-low boundary; scored conservatively as low because the
  known XSS surface is small and framed embedding was already blocked).
- **Affected commit:** `17d6313^` (the state before the remediation commit).
- **Reproduction:** `grep -r "Content-Security-Policy" deploy/ apps/web/` →
  no matches (pre-remediation).
- **Impact:** without a CSP, any future injected inline script or external
  resource reference would not be blocked by policy.
- **Remediation applied:** a conservative CSP added to the Caddyfile —
  `default-src 'self'`, `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`, `frame-ancestors 'none'`, with
  `script-src`/`style-src 'self' 'unsafe-inline'` (Next.js hydration needs)
  and `img-src 'self' data: blob:`.
- **Residual / follow-up:** the header lives in an **undeployed template**;
  the deployment rehearsal must open the site in a real browser under this
  CSP before going live, and tightening to nonce-based script-src is an owner
  follow-up. The DAST now asserts the CSP's presence so its removal fails CI.
- **Regression test:** `tools/dast/local_dast.py` §10 ("Caddyfile configures
  a Content-Security-Policy").

### Observations (no action required, recorded for completeness)

- CSRF failures surface as `422 {"code":"csrf_failed"}` rather than 403 —
  deliberate (ValidationError with a machine-readable code); consistent with
  the documented error-envelope design, not a finding.
- The traversal upload `../../evil.pdf` is **accepted and neutralized** to
  `evil.pdf` (documented behaviour in `test_api_upload_validation.py`
  "rejected **or neutralized"**); the DAST additionally proved no file
  escaped the storage root and the neutralized name round-trips.
- Not-owned and missing-resource 404 envelopes are identical except the
  per-request `request_id` (unique by design).

## 5. What this execution does NOT claim

1. **No engine-level detection claim.** The clamd daemon on instance A is a
   *protocol stub* (PING→PONG, INSTREAM→OK/FOUND). It proves MAFHUM's wiring,
   verdict handling and fail-closed policy. Real ClamAV detection is proven
   separately by `.github/workflows/production-malware-acceptance.yml` (real
   engine, real signatures, EICAR) on CI runners.
2. **Not an interactive proxy assessment.** ZAP/Burp-style crawler/spider
   coverage and a human reviewing rendered pages are out of scope for a
   scripted scan; the authenticated surface probed is the §4 table.
3. **TLS termination posture is config-level.** HSTS/CSP/XFO were verified in
   the Caddyfile template, not against a live Caddy with a real certificate
   (no Docker engine exists in this environment; see
   `docs/OPERATIONAL_VERIFICATION_REPORT.md` §2).
4. **Quota exhaustion and multi-document concurrent-upload races were not
   exercised** (destructive/slow); they are covered by the pytest suite
   (`test_api_quotas.py`, `test_api_concurrency.py`).

## 6. Remaining before "penetration tested" can be claimed

Per `docs/PENTEST_READINESS.md` §8, the full claim still requires the
staging-host pass: a real `deploy/docker-compose.prod.yml` stack with a real
ClamAV engine, real TLS and ZAP baseline + authenticated scan, executed under
a written owner authorization naming that host. This local execution is the
rehearsal for that pass: same scope table, same assertions, disposable local
target.
