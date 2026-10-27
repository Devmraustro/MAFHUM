# Threat model and security position

MAFHUM is a multi-tenant application handling students' personal study
documents. The primary adversary model is:

1. **Another authenticated user** trying to read, alter or delete someone else's
   documents.
2. **An anonymous attacker** uploading hostile files to exhaust resources, escape
   the storage directory, or execute content.
3. **A cross-site attacker** driving a logged-in student's browser.
4. **An operator mistake** — a misconfigured deployment leaking secrets or data.

Controls below are stated only where they are implemented **and** covered by a
test. Residual risks are listed at the end rather than omitted.

---

## Implemented controls

### Authentication and sessions

| Control | Implementation | Verified by |
|---|---|---|
| Password storage | scrypt with per-user salt | unit tests |
| Session tokens | Random; stored only as SHA-256 digests | unit tests |
| Session cookie | `HttpOnly`, `SameSite=lax`, `Path=/` | integration tests |
| CSRF | Double-submit cookie + `X-CSRF-Token` header on every cookie-authenticated mutation | integration tests |
| Origin validation | Parsed `scheme://host` compared against an allowlist — never substring-matched, so `https://mafhum.example.evil` cannot pass for `https://mafhum.example` | unit tests |
| Enumeration resistance | Identical `401` for unknown email and wrong password | integration tests |
| Login throttling | 12 failures then `429 rate_limited` | integration tests |
| Logout | Revokes the session server-side; returns `204` with an empty body | integration + regression test |

**Login throttling is database-enforced.** Attempts are keyed by an HMAC-derived
client identifier in `login_attempts`, so the limit holds across every API
process; `tests/test_multi_instance_state.py` asserts two independent instances
block after the same budget. Document quotas use the same pattern (row lock on
the owning user). The bounded document-processing worker is the remaining
per-process component; one worker per database is enforced by an advisory lock.

### Authorization and tenant isolation

- Ownership is enforced in the SQL `WHERE` clause on every read, download, retry
  and delete.
- A cross-user request returns `404 resource_not_found`, **byte-identical** to a
  genuinely missing resource, so existence cannot be probed.
- Verified live over HTTP: user B could not read, download, list or delete user
  A's document, and A's document survived the attempt.

### Upload handling

Uploads are treated as fully untrusted:

- Extension and browser MIME are **never** trusted; magic bytes and real parser
  behaviour decide the format.
- Server-side limits: upload size, PDF page count, DOCX entry count, DOCX
  decompressed size, archive compression ratio, extracted-text size.
- Archive shape is inspected for zip-bomb and path-traversal members.
- Encrypted PDFs are rejected rather than passed to a password prompt.
- Macros are never executed: `vbaProject.bin` in a DOCX is detected and skipped,
  never opened.
- Filenames are sanitized for display and never used to build a path. Storage
  keys are server-generated `shard/64-hex-char` values.
- Symlink and `..` traversal is refused during key resolution.
- Files are written atomically and never clobber an existing object (`O_EXCL`).
- On **any** failure after the blob is written, the blob is deleted — including
  on `BaseException`, so a cancellation cannot orphan a file.

Verified live: empty, non-PDF, signature-then-garbage, truncated and zip-bomb
shapes were all rejected `422 file_rejected`, leaving **zero** database rows and
**zero** files on disk.

### Resource exhaustion

- Fixed-size chunked reads; uploads are never buffered whole into memory.
- Bounded worker pool (`PROCESSING_WORKER_COUNT`) claiming jobs with
  `SELECT ... FOR UPDATE SKIP LOCKED`.
- Job timeout, attempt cap, and stale-job recovery so a crashed worker cannot
  lock a document forever.
- Quotas on document count, total bytes and concurrent uploads per user.

Verified live: with a limit of 3, the fourth upload returned `429`. The test
suite additionally runs an **8-thread race** against a limit of 5 and asserts
exactly 5×`201` and 3×`429` — no over-admission.

### Information disclosure

- Fixed error envelope with a generic Arabic message; no traceback, SQL, path or
  parser detail.
- Log redaction at the formatter boundary for document text, credentials,
  `Bearer`/`Basic` values, tokens and filesystem paths.
- `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- Extracted text is rendered by the UI **as text only** — never parsed as markup.
  A regression test feeds `<img src=x onerror=...>` through the whole path and
  asserts no element is created.
- Interactive API docs are disabled when `APP_ENV=production`.
- `assert_production_ready()` refuses to boot in production with the development
  session secret, without `COOKIE_SECURE`, or with a localhost database URL.

---

## Findings fixed during this phase

Each was found by verification, fixed, and given a regression test.

### 1. Logout sent a body with `204 No Content` — **Medium**

- **Component:** `api/v1/auth.py`
- **Root cause:** `JSONResponse(status_code=204, content=None)` renders
  `b"null"`. A 204 must carry no body, so the server wrote 4 bytes it had not
  declared, desynchronising HTTP keep-alive framing.
- **Impact:** the *next* request on the same connection failed with
  `RuntimeError: Response content longer than Content-Length`; the client saw a
  `500` or a reset connection. Any browser reusing a connection after logout was
  affected.
- **Why tests missed it:** `TestClient` does not reuse a raw socket, so the
  framing corruption never surfaced. Only a running server exposed it.
- **Fix:** return a bare `Response(status_code=204)` with an empty body.
- **Regression test:** `test_logout_response_has_no_body`; plus 12 live
  logout→`/me` cycles on a single keep-alive connection, all clean.

### 2. Storage root recreated world-readable at runtime — **Low/Medium**

- **Component:** `services/documents/storage.py`
- **Root cause:** `mkdir(parents=True)` creates a missing ancestor with the
  process umask. If the storage root was removed while the process ran, it came
  back `0o755` under a `0o022` umask, and only the shard directory was
  re-tightened.
- **Impact:** another local user could traverse into the storage tree. Blob files
  themselves stayed `0o600`, so contents were not readable — this was a
  traversal/exposure-of-existence issue, not a read.
- **Fix:** re-assert the configured mode on the root as well as the shard on
  every write.
- **Regression test:** `test_root_removed_at_runtime_is_recreated_private`.
- **Verified live:** storage root `0o700`, all blobs `0o600`, all names opaque
  64-character hex.

### 3. Migrations and the application read different env vars — **Low**

- **Component:** `alembic/env.py`
- **Root cause:** migrations read only `MAFHUM_DATABASE_URL` while the
  application and `.env.example` use `DATABASE_URL`.
- **Impact:** an operator could migrate one database while the application served
  another.
- **Fix:** migrations now read `MAFHUM_DATABASE_URL` then `DATABASE_URL`, and
  percent-encode safely through ConfigParser (a `%` in a password or unix-socket
  path previously raised or mangled the URL).
- **Verified:** upgrade → downgrade → upgrade round-trip on a scratch PostgreSQL
  database using `DATABASE_URL` alone.

### 4. Vulnerable `python-multipart` — **High (dependency)**

- **Component:** runtime dependency
- **Detail:** `0.0.21` carried six advisories (PYSEC-2026-1852, -3036 to -3040,
  -3038). This is the package that parses multipart uploads — directly on the
  attack surface.
- **Fix:** upgraded to `0.0.32`. `pip-audit` over the whole environment now
  reports no known vulnerabilities. The full 429-test suite was re-run after the
  upgrade.

---

## Known residual risks

Recorded deliberately. None is hidden by a passing test suite.

1. **No malware engine in the default install.** The `clamd` client, verdict
   persistence and fail-closed policy are implemented, and Gate A verified real
   detection (ClamAV + fresh signatures + EICAR-in-DOCX → API `422`) in the
   acceptance environment on 2026-10-26 (run `40377229156`). A default install
   still ships no engine or signatures, so a valid-but-malicious PDF will be
   stored and served back to its owner until an operator provisions and verifies
   `clamd`. Mitigations meanwhile: `nosniff`, `attachment` disposition and CSP.
   `docs/SECURITY_ASSESSMENTS.md` records the exact boundary.
2. **No OCR.** Scanned documents yield no text. The UI says so explicitly instead
   of showing an empty result.
3. **The processing worker pool is per-process.** Quotas and the login throttle
   are database-enforced and safe across replicas, but only one document worker
   may run per database (advisory lock). Set `PROCESSING_WORKER_ENABLED=false`
   on additional replicas before scaling out.
4. **`braces` advisory in the dev tree (GHSA-vfj7-8cjw-p6xm, high, CVSS 7.5).**
   Affects `braces <= 3.0.3`, which is the **latest release** — no patched
   version exists. It reaches MAFHUM only through
   `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch`, so
   it is lint-time only and **absent from the production tree**
   (`npm audit --omit=dev` reports 0). Exploitation needs a deeply nested brace
   glob, which in this project comes from our own committed config, not user
   input; the worst outcome is a crashed lint process. `npm audit fix --force`
   was **rejected** because it downgrades `eslint-config-next` from 16.4.2 to
   14.2.35 — a breaking change that would not fix `braces` either. Follow-up:
   re-audit when upstream publishes a fix. **Re-audit 2026-10-27:** `npm audit`
   on the full `apps/web` tree now reports **0 vulnerabilities** while
   `braces@3.0.3` is still present through `eslint-config-next`. The triage is
   retained because the code path has not changed; this is a current registry
   result, not proof the underlying issue was fixed.
5. **`pgserver` maintenance cadence.** Its license is confirmed Apache-2.0 (the
   PyPI field is blank, but the upstream `LICENSE` is standard Apache-2.0). The
   repository was last pushed in mid-2024, so it is maintained but not fast
   moving. It is dev/test only and never ships.
6. **No encryption at rest.** Filesystem permissions only.
7. **Presentation artifacts are user-controlled OOXML.** A generated `.pptx`
   could in principle contain a student's own text and is served with
   `nosniff`, `attachment` disposition and a private `Cache-Control`; it is
   never rendered server-side as HTML. Generation happens in-process, so a
   library-level parser bug would affect the API process. Bounded specs (slide,
   text, citation and output-size caps) limit the blast radius, but no
   sandbox/separate worker isolates it (ADR 0005). No visual render check is
   performed, so a font-substitution or bidi rendering defect could still reach
   the student. The PptxGenJS comparison package had two inherited high DoS
   advisories via `image-size`; they are resolved with a pinned `2.0.4`
   override and an `npm audit` CI gate, and that package never ships.
8. **Admin is a single global role, with no scoped permissions.** An admin can
   read aggregate usage and the audit trail but cannot read another user's
   documents, change roles over HTTP, or delete audit events. There is no
   per-tenant admin, no impersonation audit UI, and no separation between
   "can view metrics" and "can view user emails". Role grants happen through a
   CLI with database access, so the real perimeter is who holds database
   credentials — not the admin API. No external alerting watches the metrics
   endpoint, so anomalous admin activity is visible only if someone looks.
9. **A passing test suite does not prove the absence of vulnerabilities.** The
   suite covers the controls listed above; it is not a penetration test.

---

## Reporting a vulnerability

Please report privately to the maintainers rather than opening a public issue.
Include reproduction steps and the affected version. Do not test against a
deployment you do not own.
