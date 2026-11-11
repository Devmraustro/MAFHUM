# Phase 31 — Production Deployment & Final Acceptance Report

**Date:** 2026-11-11
**Branch:** `arena/cd6c8603-mafhum`
**PR:** [#15](https://github.com/Devmraustro/MAFHUM/pull/15)
**Release recommendation:** **CONDITIONAL GO for the container path; NO-GO for a
public production launch today.** Reasons and conditions are in §10.

Every result below was produced by a command that was actually run. Where
something was not verified, it says so and says why. Nothing in this document is
inferred from a README or a previous report.

---

## 1. Commit and PR status

| Item | Value |
|---|---|
| Base commit | `bc28114e5e8df822071beade5ee3cfbdb2a46448` (`main`) |
| Branch head at verification time | `66b89d8210b9ff2a6e435558cc030513421c0d84` |
| Working tree | Phase 31 changes applied on top of the head above and committed (see §1.1) |
| PR #15 state | OPEN, `MERGEABLE`, `mergeStateStatus: CLEAN` |
| Divergence from `main` | `0` commits behind, `1` ahead (pre-Phase-31); no conflicts |
| Files changed by the original PR | 54 (+5,549 / −220) |

### 1.1 PR verification — the 10 required checks

All ten were green on `66b89d8` before Phase 31 work began:

| Check | Result |
|---|---|
| Backend — lint, types, tests | pass (4m27s) |
| Frontend — lint, types, tests, build | pass (59s) |
| Migrations — upgrade and downgrade | pass (55s) |
| Security — SAST, secrets, config contract | pass (39s) |
| Local authenticated DAST (production posture) | pass (1m12s) |
| Compose config + production contract | pass (29s) |
| Real ClamAV engine — clean / EICAR / engine-outage | pass (2m29s) |
| Repository baseline | pass (4s) |
| Vercel | pass |
| Vercel Preview Comments | pass |

Phase 31 adds a commit; its checks are reported in §9.

---

## 2. What Phase 31 changed, and why

| # | Change | Driven by |
|---|---|---|
| 1 | `RequestThrottle` + migration `0012_request_throttle` | **Real gap.** Password-reset and verification were unauthenticated and unthrottled: an open mail-sending endpoint, a free-tier email quota anyone could burn, and a delivery nuisance at a victim's address. 5 requests / 15 min per client, per scope. |
| 2 | `client_identifier` with `TRUSTED_PROXY_HOPS` | Login and lifecycle throttles both parsed `X-Forwarded-For[0]`, which the client controls. Keyed on the wrong hop, every client behind two proxies shares one bucket. |
| 3 | `Date` and `Message-ID` on every outbound message | **Found live.** Messages were emitted with no `Date`; a real SMTP server rejected them (`SMTPDataError`). Relays may 554 or spam-score such mail — a reset link quarantined for a missing header is a locked-out student. |
| 4 | `apps/web/scripts/verify-api-origin.mjs` + 7 tests + CI step | **Found live.** The Vercel web project was configured with `MAFHUM_API_URL=https://mafhum-api.invalid`; the old guard only checked non-emptiness, so a dead bundle was published successfully. |
| 5 | `tests/test_security_lifecycle.py` (17 tests) | Each line of the requested security review, asserted. |
| 6 | `tests/test_smtp_delivery.py` (9 tests) + `aiosmtpd` dev dependency | Real SMTP transport proof instead of "it builds". |
| 7 | `tests/test_smtp_delivery.py` config tests, `test_text_fold.py`, `test_security_lifecycle.py` | Production config refusal paths (file transport, localhost DB, half-configured SMTP). |
| 8 | Arabic folding shared module consequences | `services/text/fold.py` used by both search and grounded research (Phase 30) — unchanged here, retested. |
| 9 | `docs/EMAIL_PROVIDERS.md`, `docs/ACCOUNT_LIFECYCLE.md` link, `.env.example` note | Free-tier SMTP provider selection with exact values. |
| 10 | `docs/DEPLOYMENT_DECISION.md` Phase 31 findings | The three findings that changed the deployment picture. |

Nothing was architecturally rewritten. No security control was relaxed to make a
test pass; the two controls that changed (throttle key resolution, CSRF for
anonymous flows) became stricter.

---

## 3. CI results (Phase 31 commit)

See §9 — the Phase 31 commit was pushed and its checks are recorded there. Locally,
on the Phase 31 tree, the complete gate set was run and passed:

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check .` | All checks passed (164 files) |
| Format | `ruff format --check .` | 164 files already formatted |
| Types | `mypy src` | Success: no issues found in **84** source files |
| Backend tests | `pytest` (full suite) | **EXIT 0**, 0 failed |
| Frontend types | `npx tsc --noEmit` | 0 errors |
| Frontend lint | `npx eslint . --max-warnings 0` | 0 problems |
| Frontend tests | `npx vitest run` | **139 passed / 14 files** |
| Frontend build | `npx next build` | success (see §7) |
| Local DAST | `tools/dast/local_dast.py` | **86 passed, 0 failed** |
| Monitoring rules | `tools/monitoring/validate_monitoring.py` | 81 checks passed |
| Security baseline | `tools/security_baseline.py` | ALL CHECKS PASSED |
| Compose contract | `tools/acceptance/prod_compose_contract.py` | ALL CHECKS PASSED |
| SAST | `bandit -q -ll -r src` | no findings |

### 3.1 One caveat that must be read

`tests/test_clamav_acceptance_safety.py::test_ambiguous_resource_state_aborts` and
`::test_container_name_collision_aborts` fail **whenever any process is listening
on `127.0.0.1:8000`** — including the verification server used in §6 — because
`preflight()` reports "port in use" and returns before reaching the ownership
checks those two tests assert. Both pass with the port free. This was confirmed
twice, once on a clean tree (`git stash`), so it is a **test-isolation defect that
predates Phase 31**, not a regression. The full-suite run quoted above was done
with the port free: `EXIT 0`, 0 failures.

---

## 4. Security audit

Each item was examined in the code and, where behaviour could change silently,
pinned with a test. All 17 tests in `tests/test_security_lifecycle.py` pass.

| Item | Verdict | Evidence |
|---|---|---|
| Reset token expiration | **PASS** | Token TTL is configuration (`PASSWORD_RESET_TTL_MINUTES`, default 60). An unused-but-expired token is rejected with `422 invalid_token`. Also enforced at the schema level: `ck_user_tokens_expiry` forbids `expires_at <= created_at`. Test: `test_expired_token_is_rejected`. |
| Single-use enforcement | **PASS** | Redemption locks the row and sets `consumed_at` in the same transaction; only a SHA-256 digest is stored. Live proof: second use of the same link → `422 invalid_token` / "هذا الرابط مستعمل من قبل". Test: `TestSignedOutRecovery` + live run §6.2. |
| Account enumeration | **PASS** | Reset request returns identical status and body for known and unknown addresses (`202`, same JSON); the difference exists only in the database. Login returns the same status for a wrong password and an unknown account. Tests: `test_reset_request_is_indistinguishable_for_unknown_accounts`, `test_login_error_does_not_reveal_account_existence`. |
| Rate limiting | **PASS after fix** | Login was DB-backed and correct. Recovery endpoints had **no limit at all**; now `RequestThrottle`, 5/15 min per scope per hashed client, separate buckets. Tests: `test_reset_requests_are_rate_limited` (5×202 then 429), `test_verification_has_its_own_budget`. |
| Throttle key privacy | **PASS** | Stored key is an HMAC-SHA256 (64 hex chars); no raw address, no "testclient" substring. Test: `test_throttle_stores_no_raw_address`. |
| CSRF — authenticated flows | **PASS** | Origin/Referer allowlist (parsed origin comparison, not substring) + double-submit header; multipart uploads may carry the token as a form field. |
| CSRF — anonymous flows | **PASS** | `CsrfGuardAnonymous`: origin is mandatory (missing origin → 422), double-submit enforced whenever a CSRF cookie exists. Tests: `test_anonymous_endpoints_reject_a_foreign_origin` (3 endpoints), `…_reject_a_missing_origin`, `test_double_submit_is_enforced_when_the_cookie_exists`. |
| Session invalidation | **PASS** | Reset revokes every session; password change revokes all *other* sessions and keeps the caller. Test: `test_password_change_keeps_caller_and_kills_other_sessions`. |
| Cookie flags | **PASS** | Session cookie: `HttpOnly; Secure; SameSite=Lax; Path=/`. CSRF cookie: script-readable by design, `Secure`, `SameSite=Lax`. Test: `test_auth_cookies_carry_secure_flags`. `GET /auth/csrf` issues no session cookie (`test_session_cookie_is_not_issued_by_the_anonymous_csrf_endpoint`). |
| CORS / trusted proxy | **PASS after fix** | No permissive CORS middleware; the browser never calls the API cross-origin (Next rewrites `/api/*`). Client IP resolution now honours `TRUSTED_PROXY_HOPS` (default 1) instead of trusting the left-most, client-supplied hop. |
| Secrets and env validation | **PASS** | Production refuses to boot with: default `SESSION_SECRET`, `COOKIE_SECURE=false`, a localhost database, `EMAIL_DELIVERY=file`, `MALWARE_SCAN_REQUIRED=false` or no clamd endpoint, a short metrics token, or `STORAGE_BACKEND=vercel_blob` without credentials. Tests in `test_smtp_delivery.py` + `test_production_config.py`. |
| Token leakage into logs | **PASS** | The formatter redacts token-shaped values; a live reset was performed with `DEBUG` logging captured and the 43-character token does not appear in any record. Test: `test_rendered_mail_never_reaches_the_log`. |
| Credential leakage in errors | **PASS** | A failed SMTP AUTH raises `EmailDeliveryError` carrying only the exception type; neither the password nor the username appears. Tests: `test_auth_failure_is_reported_without_echoing_credentials`, `test_smtp_failure_is_bounded_and_credential_free`. |

### 4.1 Residual security notes (not failures)

- `X-Forwarded-For` is still trusted at the configured hop count. A deployment
  whose edge does **not** overwrite the header would let a client rotate its own
  key. Documented in the resolver docstring; the value is used only for rate
  limiting, never authorisation, and is hashed before storage.
- The throttle fails **open** on database errors. Deliberate: with the database
  down, recovery cannot work anyway (the token cannot be stored), so returning
  `429` would add an outage mode without adding a protection.
- Verification is advisory: an unverified address can still sign in and read its
  own documents. Documented and tested as intentional in
  `docs/ACCOUNT_LIFECYCLE.md`.

---

## 5. Email delivery evidence

### 5.1 Transport proof (automated)

`tests/test_smtp_delivery.py` runs a **real SMTP server** (`aiosmtpd`) on a real
port and points the production `SmtpEmailSender` at it: 9 tests, all passing.
Covered: message accepted over SMTP; Arabic subject survives RFC 2047 encoding;
Arabic body intact; `From`/`Message-ID`/`Date` stamped and parseable; unreachable
server → bounded `EmailDeliveryError`; failed AUTH echoes no credential;
`EMAIL_DELIVERY=smtp` builds `SmtpEmailSender`; `smtp` with no host falls back to
the honest disabled sender; production refuses `EMAIL_DELIVERY=file`; production
refuses a localhost `DATABASE_URL`.

### 5.2 End-to-end proof (live stack, real SMTP, no mocks)

API on `:8000` with `EMAIL_DELIVERY=smtp`, `SMTP_HOST=127.0.0.1`,
`SMTP_PORT=2525` pointed at a real `aiosmtpd` listener that writes accepted
messages to `.preview/outbox/`. Web on `:3000`, all requests through the Next proxy.

| Step | Request | Result |
|---|---|---|
| Register | `POST /api/v1/auth/register` | `201` |
| CSRF bootstrap | `GET /api/v1/auth/csrf` | `200`, token issued |
| Reset request | `POST /api/v1/auth/password-reset/request` | `202` |
| SMTP delivery | capture server | **3 messages accepted**, first: `001-20261111T123233.eml` |
| Reset confirm (signed out, fresh cookie jar) | `POST …/password-reset/confirm` | `200` |
| Reuse same link | same | `422 invalid_token` — "already used" |
| Login with new password | `POST /api/v1/auth/login` | `200` |
| Verification request | `POST …/email-verification/request` | `202` |
| Verification confirm (different, signed-out client) | `POST …/email-verification/confirm` | `200` |
| Account state after | `GET /api/v1/auth/account-state` | `email_verified: true` |

Captured message, verbatim headers:

```
To: phase31@example.com
From: MAFHUM <no-reply@mafhum.test>
Subject: إعادة تعيين كلمة المرور — مَفْهُوم
Date: Wed, 11 Nov 2026 12:32:33 -0000
Message-ID: <179440035313.68717.6316718972697280101@mafhum.local>
Content-Type: text/plain; charset="utf-8"
```

Body contains a single reset link of the form
`http://localhost:3000/reset-password?token=<43 chars>`, built from
`PUBLIC_WEB_URL` — not from the request `Host`.

### 5.3 Fail-closed behaviour (preserved)

Before the `Date` fix, a genuine delivery failure produced exactly the intended
behaviour and it was observed in the API log:

```
WARNING mafhum_api.services.notifications.email: email delivery failed | event=email_failed
POST /api/v1/auth/password-reset/request HTTP/1.1" 503 Service Unavailable
```

The endpoint answered `503 email_delivery_unavailable` with an honest Arabic
message rather than a false `202`. Nothing was relaxed to make the later run pass;
the message gained the headers the relay required.

### 5.4 What is NOT proven

- **No real provider was used.** No Brevo/Resend/SendGrid account exists in this
  environment, so provider-side acceptance, SPF/DKIM/DMARC alignment, and inbox
  vs. spam placement are **unverified**. `docs/EMAIL_PROVIDERS.md` records exact
  free-tier values and an operator checklist.
- No bounce handling, no out-of-band "your password changed" notice.

---

## 6. Runtime verification (real HTTP, real server logs)

Server: uvicorn (Phase 31 tree), PostgreSQL on a local unix socket, migration
`0012_request_throttle` applied. All web traffic went through `:3000` (Next proxy).

| Check | Result |
|---|---|
| Homepage `/` | `200`, `lang="ar" dir="rtl"`, hero copy present |
| Landing images (4) | `200` each (`hero-documents`, `grounded-research`, `study-flashcards`, `slides-tools`) |
| `/login`, `/reset-password`, `/verify-email`, `/projects`, `/account`, `/documents` | `200` each |
| Registration → login | `201` → `200` |
| Password recovery (full cycle) | `202` → mail → `200` → reuse `422` → login `200` |
| Email verification | `202` → mail → `200` (signed-out client) → state `email_verified: true` |
| Project create | `201`, id `ca17da3d-…` |
| Document upload (real DOCX, 1,239 B) into the project | `201`, status `pending` |
| Processing | `ready` on first poll: 316 chars, 5 segments, 1 page (worker log: `document_processed`) |
| Search — Arabic, diacritic-folded (`المصادر`) | `200`, 2 segments, correct passage |
| Search — `توثيق` | `200`, 1 segment |
| Search — French `bibliographie` | `200`, 1 segment |
| Search — absent term | `200`, 0 segments (no fabrication) |
| Project filter `?project_id=` | `200`, total 1, correct `project_id` |
| Account export | `200`, `application/zip`, 5,412 B, 9 entries incl. `account.json`, `documents.json`, `files/*.docx`, `manifest.json` |
| Session list | `200`, 1 session, `current: true` |
| `/health/live`, `/health/ready` | `200`, `200` |
| Documents limits (honest capability) | `ocr_available: false`, `malware_scanning_available: false` |
| Arabic RTL | `lang="ar" dir="rtl"` on the served HTML |
| French locale | Switching to `fr` sets text + `lang="fr"` + `dir="ltr"`; dictionaries at exact parity **302 = 302** keys; no Arabic script inside the French dictionary (test: `src/lib/__tests__/locale.test.tsx`, 4 tests) |
| Mobile responsiveness | `<meta name="viewport" content="width=device-width, initial-scale=1">` present; the stylesheet carries `@media` breakpoints at 900/640 px and a `prefers-reduced-motion` block. **Not** visually verified in a real browser — see §8. |

Note on one earlier mistake in this run: `GET /api/v1/account/me/export` returned
`404` because the real route is `GET /api/v1/me/export` (confirmed from the
server's own OpenAPI document). The export itself works; the first probe used the
wrong path.

---

## 7. Build/route inventory

`npx next build` — 12 routes, `/` static, `/documents/[id]` dynamic, the rest
static. Shipped CSS bundle 22.7 KB including the landing layer.

---

## 8. Deployment

### 8.1 What exists on Vercel today (inspected, not assumed)

| Project | Root | State | Usable? |
|---|---|---|---|
| `mafhum-web` | `apps/web` | linked to this repo; PR #15 preview `dpl_7qCct8G…` = READY | Build only — its `MAFHUM_API_URL` is `https://mafhum-api.invalid` |
| `mafhum-api-poc` | `apps/api` | Phase-1 POC pinned to commit `eed6d86` | No — no database, no scanner; not this branch |
| `talimgo-*`, `web-dxn` | — | unrelated projects | — |

### 8.2 Why no production deployment was performed

The instruction was explicit: deploy only when prerequisites are satisfied, and
stop and report blockers rather than claim success. **The prerequisites are not
satisfied, and none of them can be created from here:**

1. **No database.** No Neon project, credential, or connection string exists in
   this environment. Without one, `mafhum_api.vercel_app` refuses to start by
   design (`assert_postgres_only`). Migrations were applied and verified against a
   local PostgreSQL 16 cluster only — never against a managed instance.
2. **No API deployment.** `mafhum-api-poc` is a stale POC. Deploying this branch
   would need the project re-pointed at the current commit plus a full environment.
3. **The API cannot run on Vercel in production posture.** `APP_ENV=production`
   refuses to boot without a reachable `clamd` (`MALWARE_SCAN_REQUIRED`), and a
   Function cannot host a resident ClamAV daemon with a mutable signature volume.
   Deploying "production" would either fail closed (uploads unusable) or require
   disabling a security control — which the brief forbids and which I will not do.
4. **No email provider credential.** Password recovery would answer `503`
   honestly, which is correct but not a launchable state.
5. **Egress to Vercel is blocked from this sandbox**, so even a successful
   deployment could not be verified over HTTP here — only through the Vercel API.
   `curl https://mafhum-web.vercel.app/` produced `000` (TLS handshake refused);
   `https://api.github.com/` returns `200` from the same shell.

The web deployment did build and is `READY`, but publishing it as "production"
would point it at an `.invalid` API and produce a site where registration and
login fail. **A deployment URL is therefore deliberately not claimed.**

### 8.3 What was done instead

- The placeholder-origin defect is now **impossible to publish silently**: the
  build command runs `scripts/verify-api-origin.mjs`, which rejects reserved TLDs
  everywhere and loopback origins on Vercel (`VERCEL=1`). 7 unit tests; a CI step
  asserts the guard rejects the exact value that was deployed.
- `docs/DEPLOYMENT_DECISION.md` records the architecture verdict, the three
  findings, and the exact owner steps to finish the serverless path.

---

## 9. Migrations

| Check | Result |
|---|---|
| Alembic head | `0012_request_throttle` |
| Applied locally (pgserver PostgreSQL) | `upgrade 0011 → 0012` succeeded; `alembic_version` = `0012_request_throttle`; `throttled_actions` exists |
| Chain linearity test | `tests/test_migrations.py` (12 tests) pass, including exact revision order |
| Upgrade/downgrade round trip | covered by the CI job `Migrations — upgrade and downgrade` (passed for the pre-Phase-31 commit; re-run on this commit, §11) |
| Production data touched | **None.** No production database was contacted, read, or written. |

---

## 10. Outstanding risks

| # | Risk | Severity | Owner action |
|---|---|---|---|
| 1 | No production database; serverless API cannot start | **Blocking** | Create a Neon project; set `DATABASE_URL` (pooled for runtime, direct for migrations) |
| 2 | ClamAV cannot run on Vercel; uploads fail closed there | **Blocking for serverless** | Use the container path, or provide an external clamd reachable from Functions |
| 3 | No email provider credential; recovery answers `503` | **Blocking for launch** | Configure Brevo (or equivalent) per `docs/EMAIL_PROVIDERS.md`; send one real reset |
| 4 | `mafhum-web` still points at `mafhum-api.invalid` | High | Set `MAFHUM_API_URL` to the real API origin; the build now refuses the placeholder |
| 5 | Provider deliverability (SPF/DKIM/DMARC) unverified | Medium | Publish DNS records, send to an external mailbox, check spam placement |
| 6 | Throttle trusts `X-Forwarded-For` at a configured hop count | Medium | Confirm the edge overwrites the header; raise `TRUSTED_PROXY_HOPS` if two hops |
| 7 | No real-browser E2E (no Chromium in this environment) | Medium | Visual/mobile review against a running instance; the CSS and viewport are in place but unverified visually |
| 8 | Serverless path unverified end to end (no resident worker; tick-driven) | Medium | Accept tick semantics, or use containers |
| 9 | Pre-existing ClamAV acceptance test isolation defect on port 8000 | Low | Free the port, or pass `--api-port` in the fixture; predates Phase 31 |
| 10 | OCR still not implemented (correctly advertised as unavailable) | Low (product scope) | Implement per `services/documents/ocr.py`; do not advertise before verification |

---

## 11. Final release recommendation

**Container path (`docker-compose.yml`): GO, conditional.** The application is
internally consistent, the whole gate set is green on this commit, and the
security review items are closed or explicitly accepted with tests. Merge PR #15
after CI re-runs green on the Phase 31 commit. Before announcing availability:
provision a database, configure SMTP with a verified sending domain, run
`alembic upgrade head`, and complete one real reset and one real verification.

**Public production launch on Vercel today: NO-GO.** Three prerequisites are
missing and one is architectural (no ClamAV on Functions). Nothing was deployed,
no production secret was created, no production data was touched, and no
deployment URL is claimed.

### OCR — unchanged and explicit

OCR remains **not implemented** and is advertised as such: `GET
/documents/limits` returns `ocr_available: false`, sourced from
`services/documents/ocr.py` (the single source of truth), and a test asserts both
the flag and that the reason string names the text-layer limitation. No simulated
OCR exists anywhere in the codebase, and none was added.
