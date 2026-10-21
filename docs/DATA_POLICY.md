# MAFHUM Data Policy (Implementation Requirements & Phase 2 Lifecycle)

## User data
Collect only data needed for accounts, saved work, usage and support:
- Account identity (`email`, hashed password, optional `full_name`, `preferred_locale` in `ar` | `fr`, account status, project quota counters, timestamps).
- Passwords are never stored or logged in plaintext; only SHA-256 pre-hashed salted `bcrypt` digests (`pyca/bcrypt`, cost 12) are persisted.
- Session tokens are never stored in plaintext; only secret-keyed `HMAC-SHA256` digests are stored server-side.
- Every project and document has explicit server-enforced ownership (`owner_id`). Client-supplied user or owner IDs are never trusted.
- Do not train on student documents by default. Do not send uploads to external inference providers unless configured and disclosed.

## Account export, deletion, and honest retention boundaries (Phase 2)
- **Export (`GET /api/v1/me/export`)**: Authenticated students can export their profile metadata and all owned project records as JSON without exposing password hashes or session secrets.
- **Deletion (`DELETE /api/v1/me`, CSRF-protected)**: Atomically revokes and deletes all active/historical server sessions for the user, deletes all owned `projects` records in the primary relational database, deletes the `users` record, and clears browser session/CSRF cookies immediately.
- **Retention & external limitations (explicitly disclosed, never overstated)**:
  1. Encrypted database backups and infrastructure-level redacted access/error logs follow their scheduled operational retention rotation and are **not** retroactively rewritten at the instant `DELETE /api/v1/me` runs.
  2. In Phase 2, no third-party authentication or external AI provider receives user data (`AI_PROVIDER=mock`). When optional external providers or object-storage buckets are introduced in later phases, deletion guarantees only cover stores explicitly purged and verified by deletion jobs — MAFHUM never claims deletion of data residing in backups or external systems without proof.

## Uploads and retention (Phase 3+)
Validate uploads and store privately. Record provenance and processing status. Define retention by data class and implement deletion of originals, extracted text, chunks, embeddings, caches and generated artifacts. Do not put document contents or credentials in ordinary logs.

## Source use
Each connector documents terms, access boundary, allowed metadata/full text/excerpts, attribution, update cadence and takedown handling. No paywall/authentication bypass. Link to restricted content rather than indexing it unless permitted. Keep canonical URL, checksum and source/version dates.

## AI processing
All output is a draft. Use a provider-neutral gateway and disclose configured provider/data path. Do not claim data stays local unless actual runtime configuration proves it. Enforce server-side quotas and output limits.

## Academic integrity
Every citation must resolve to a source record and supporting excerpt or page/section when available. Never invent authors, pages, URLs, titles, quotations or legal provisions. Mark unverified details for student review and encourage reading original sources.

## Logging/access
Least privilege, redacted logs, audit privileged access, and avoid storing prompts/responses unless required for saved work and covered by retention rules.

Before public release, review this policy against applicable Algerian law, provider terms and actual deployment. This document is not legal advice or a compliance certification.
