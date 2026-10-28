# Data policy

What MAFHUM stores, where it stores it, who can reach it, how long it is kept,
and what it explicitly cannot promise.

This document describes the **implemented** behaviour. Where a control is absent,
that is stated plainly rather than implied by omission.

---

## 1. What is collected

| Data | Why | Required |
|---|---|---|
| Email address | Account identity and login | Yes |
| Password | Authentication — stored only as an scrypt hash, never in plaintext | Yes |
| Display name | Shown in the interface | No |
| Uploaded documents | The core function of the product | Yes, by user action |
| Text extracted from those documents | Search and study features | Derived |
| Academic source metadata | Cataloguing | Only what the user supplies |
| Session tokens | Keeping a user signed in | Yes |
| Presentation job specs | Remembering which citations a deck was built from | Only when a deck is created |
| Generated PPTX artifacts | Delivering an editable deck to its owner | Only when a deck is created |

MAFHUM collects no telemetry, no analytics, no advertising identifiers and no
third-party tracking. There is no external service to send data to.

**Phase 7 audit trail.** `audit_events` stores an action name, an outcome, an
opaque actor id, optional target/request ids, a timestamp and an allow-listed
`details` object. It is append-only at the API level. It never stores an email,
password, filename, URL, IP address, document text or generated deck content.
When an account is deleted, `actor_id` becomes `NULL`, so the event survives
without identifying the erased person. `GET /api/v1/admin/usage` is the only
view that returns an account email, and it requires the admin role.

**Retention is not implemented.** MAFHUM does not expire audit rows or
backups. Setting a retention schedule and deleting expired archives is an
operator responsibility.

**Never invent bibliographic data.** Source records distinguish
`user_supplied`, `document_extracted` and externally verified provenance. A
field the user did not provide and the document did not contain is stored as
`NULL` — it is never guessed, inferred or backfilled.

---

## 2. Where it is stored

- **Database:** PostgreSQL. Passwords are scrypt hashes; session tokens are
  stored only as SHA-256 digests, so a database read does not yield a usable
  session.
- **Document files:** the local filesystem under `STORAGE_ROOT`
  (default `./data/mafhum-storage`), **outside any static or public directory**.
  - Directories are `0o700`, files `0o600`.
  - Files are named with an opaque server-generated `shard/64-hex-char` key.
    The user's filename is kept in the database for display only and is never
    used to build a filesystem path.
  - **No absolute filesystem path is ever stored in a database row or returned
    by the API.**
  - Files are written atomically (`O_CREAT|O_EXCL` + `fsync` + rename) so a crash
    cannot leave a half-written object that later reads as valid.
- **Generated presentations:** PPTX files are private blobs in the same
  `STORAGE_ROOT`, with the same opaque keys and permissions. A job's stored spec
  keeps the verbatim quotations and citation locators so source availability can
  be recomputed on read. Deleting a job removes its blob; deleting an account
  erases all presentation blobs before dropping the rows.
- **Backups:** none are performed by MAFHUM. Backing up is an operator
  responsibility. A backup of the database alone is **not** a backup of user
  documents — `STORAGE_ROOT` must be captured too, and both must be restored
  together or records will point at missing files.

There is **no** object storage, CDN, or external storage integration in the
default configuration.

---

## 3. Who can access it

Every document, project and source record is owned by exactly one user.
Ownership is enforced **server-side in the SQL `WHERE` clause**, not in the
interface.

A request for another user's resource returns `404 resource_not_found` —
**byte-identical** to the response for a resource that does not exist. The API
therefore cannot be used to discover whether a given document ID belongs to
someone.

Downloads are served through an authorized endpoint with
`Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`. There is
no public static file serving of user content, and no unauthenticated URL from
which a document can be fetched.

---

## 4. Retention and deletion

- Documents are kept until the user deletes them or deletes their account. There
  is no automatic expiry.
- **Deleting a document** removes the database row, its text segments, its
  processing jobs, its source records, and the file on disk.
- **Deleting an account** removes the user, their sessions, projects, documents,
  segments, jobs, source records and files, and reports what was deleted.
- Deletion is **resumable**: if removing a file from disk fails, the operation is
  recorded and can be retried rather than silently half-completing. Tests cover
  this partial-failure path explicitly.
- Orphan reconciliation is available for files left on disk without a
  corresponding record.

**What deletion does not cover:** copies an operator took in a backup, or a
document the user already downloaded. MAFHUM cannot retract those.

---

## 5. Logging

Logs are redacted at the formatter boundary. Verified by tests:

- No document text or filename content.
- No passwords, session tokens, CSRF tokens, or `Bearer`/`Basic` credentials.
- No filesystem paths or storage keys.
- No tracebacks, SQL, or parser internals in error responses.

Error responses use a fixed envelope — `{code, message, request_id}` — with a
generic Arabic message and no internal detail. A `request_id` is returned so an
operator can correlate a report with a log line without the user seeing
internals.

---

## 6. What MAFHUM does **not** do

These are limitations, stated so nobody has to guess:

1. **A default *development* install still ships no engine.** Uploads are checked
   by magic bytes, real parser behaviour, archive shape, entry counts,
   decompressed-size limits and page limits. That is not antivirus. A malicious
   file that is also a valid PDF will be stored with `scan_verdict=not_scanned`
   unless an operator points `CLAMD_HOST`/`CLAMD_UNIX_PATH` at a real daemon. The
   **production** blueprint now includes a real `clamd` service and hard-codes
   `MALWARE_SCAN_REQUIRED=true`; production refuses to boot with that flag off or
   with no daemon address. Real EICAR detection is verified in CI (staging Gate A
   and `production-malware-acceptance.yml`), not on the owner's host — the
   operator must verify their own engine and signature freshness before claiming
   malware protection.
2. **No OCR.** Scanned or image-only PDFs are detected and reported; no text is
   extracted from them.
3. **No URL ingestion.** MAFHUM never fetches a document from a URL, so it cannot
   be used as an SSRF vector — and cannot ingest from a link either.
4. **No external AI provider by default.** `ENABLE_AI_GATEWAY` is `false`. No
   document content leaves the server. If that is ever enabled, it must be
   disclosed to users in the interface before their content is sent anywhere.
5. **No application-layer encryption at rest is enabled.** Files are stored with
   `0600` permissions on a private volume and passwords are scrypt-hashed, but
   anyone who obtains the database files, a dump or the blob volume can read user
   content. A design and a tested AEAD interface exist in
   [`ADR/0006-encryption-at-rest.md`](ADR/0006-encryption-at-rest.md); **no
   production column is encrypted and no KMS is configured.** Volume/disk
   encryption remains an operator responsibility.
6. **Quotas are database-enforced; the worker is single-writer.** Document counts,
   byte totals and the login throttle hold across API replicas. Only the
   in-process processing worker is limited to one per database (advisory lock);
   disable `PROCESSING_WORKER_ENABLED` on additional replicas.

---

## 7. User rights in practice

| Right | How |
|---|---|
| Access your data | Download the original file at any time |
| Correct metadata | Edit your source records |
| Delete a document | From the interface; removes rows, segments and the file |
| Delete your account | Removes everything listed in section 4 |
| Export | `GET /api/v1/me/export` streams a bounded ZIP of **your own** rows and original blobs — session-authenticated, ownership-filtered in SQL, capped by file count, bytes and time. See §8. |
| Retention | Audit events are kept indefinitely by default; a bounded, opt-in purge is described in §8. |

---

## 8. Export scope, audit retention and privacy

### What the export contains

`GET /api/v1/me/export` is session-authenticated and GET-only. Every query is
filtered by `owner_id` in SQL; no identifier from the request can select another
account. The ZIP contains `account.json`, `projects.json`, `documents.json`,
`sources.json`, `presentations.json`, `study_topics.json`, a `manifest.json`
mapping archive member names back to original filenames, a bilingual README, and
the caller's original uploaded blobs plus generated PPTX artifacts under
`files/`.

**Never included:** password hashes, session tokens, CSRF secrets, login attempts,
other users' rows, audit events and extracted text segments (the original files
are included instead). Archive member names are UUIDs with a whitelisted
extension — a user-controlled filename can never become a ZIP path.

Bounds are enforced while the archive is built: `EXPORT_MAX_FILES`,
`EXPORT_MAX_FILE_BYTES_MB`, `EXPORT_MAX_TOTAL_BYTES_MB`, `EXPORT_MAX_SECONDS` and
`EXPORT_SPOOL_MEMORY_MB`. Only the spool threshold is ever held in memory; larger
archives spill to a private `0600` temporary file and are streamed back in
64 KiB chunks. If a cap is reached, `manifest.json` sets `truncated: true` and
lists the reason under `notices` — the export is never silently incomplete.

### What deletion does and does not remove

`DELETE /api/v1/me` tombstones the account, revokes sessions, then removes rows
and blobs in two phases separated by a commit. Audit events survive with a NULL
`actor_id` so the security trail is not erased with the account. **Backups are
not purged** by account deletion: an archived backup may still contain the data
until the backup itself expires. This is a residual-data limitation, disclosed
rather than hidden.

### Audit-log retention policy (owner decision required)

`audit_events` rows are kept indefinitely by default
(`AUDIT_RETENTION_DAYS=0`). A positive value is **only an input** to the operator
command `python apps/api/tools/purge_audit.py --days N`; nothing deletes rows
automatically and no HTTP endpoint can delete them. The command is a **dry run**
unless `--apply` is given, deletes only `audit_events` rows older than the cutoff
in independently committed batches of at most `AUDIT_PURGE_BATCH_SIZE`, and stops
after `AUDIT_PURGE_MAX_BATCHES` batches. There is no cascade and no TRUNCATE.
Each purge writes its own `audit_events_purged` row so the deletion is itself
auditable.

Before setting a positive retention window the owner must confirm:

- the applicable legal/academic record-keeping period;
- whether any **legal hold** applies (if it does, do not run the purge at all;
  there is no per-record hold flag in the current implementation);
- whether backups containing older audit rows may be retained for longer;
- who is authorised to run `--apply`, and how each run is logged.

Until that decision is made, `0` is the correct and safe value. A scheduled purge
with `--apply` must not be enabled by default.

### Residual privacy risks

- No application-layer field encryption is enabled (see §6 and
  [ADR 0006](ADR/0006-encryption-at-rest.md)).
- Volume/disk encryption, backup encryption and key escrow are operator tasks.
- Audit `details` are allow-listed and bounded, but `actor_id` and timestamps are
  still personal data in the sense of most privacy regimes.
- Log aggregation and retention are not configured; application logs may contain
  request ids and error codes but must never contain document text.
