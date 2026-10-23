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

MAFHUM collects no telemetry, no analytics, no advertising identifiers and no
third-party tracking. There is no external service to send data to.

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

1. **No malware scanning in a default install.** Uploads are checked by magic
   bytes, real parser behaviour, archive shape, entry counts, decompressed-size
   limits and page limits. That is not antivirus. A malicious file that is also a
   valid PDF will be stored, and its `scan_verdict` is recorded as `not_scanned`.
   An operator can connect an external `clamd` and require a clean verdict, in
   which case anything other than a confirmed-clean result rejects the upload.
   Detection itself has not been verified here — no engine was available — so do
   not read the presence of the setting as evidence that scanning works.
2. **No OCR.** Scanned or image-only PDFs are detected and reported; no text is
   extracted from them.
3. **No URL ingestion.** MAFHUM never fetches a document from a URL, so it cannot
   be used as an SSRF vector — and cannot ingest from a link either.
4. **No external AI provider by default.** `ENABLE_AI_GATEWAY` is `false`. No
   document content leaves the server. If that is ever enabled, it must be
   disclosed to users in the interface before their content is sent anywhere.
5. **No cross-region residency or encryption at rest.** Files are stored with
   filesystem permissions only. Full-disk encryption is an operator concern.
6. **Single-process quotas.** Document counts, byte totals and the processing
   worker are enforced per process. Running multiple API replicas without moving
   enforcement into the database would let a user exceed their quota.

---

## 7. User rights in practice

| Right | How |
|---|---|
| Access your data | Download the original file at any time |
| Correct metadata | Edit your source records |
| Delete a document | From the interface; removes rows, segments and the file |
| Delete your account | Removes everything listed in section 4 |
| Export | Download your original files; there is no bulk export endpoint yet |
