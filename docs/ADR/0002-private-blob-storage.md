# ADR 0002: Private, opaque-key blob storage for uploaded documents

- Status: Accepted
- Date: 2026-10-22

## Context
Students upload personal study documents. Uploaded files are untrusted input and
are also private data. Serving them from a static directory, or naming them after
the user's filename, would make authorization, path traversal and enumeration
problems structural rather than incidental.

## Decision
Store every upload under a **server-generated opaque key** (`shard/64-hex-char`)
beneath a `STORAGE_ROOT` that sits outside any static or public directory.
Directories are `0o700` and files `0o600`. Writes are atomic
(`O_CREAT|O_EXCL` + `fsync` + rename). No absolute filesystem path is stored in a
database row or returned by the API; the user's filename is kept for display only
and never used to build a path. Every read goes through an endpoint that checks
ownership first. Storage sits behind a `DocumentStorage` protocol so an object
store can replace the local backend without changing callers.

## Consequences
- A leaked database row does not reveal where a file lives, and a guessed
  filename cannot reach another user's document.
- There is no CDN or static-serving path, so every download is authorized and
  auditable — at the cost of streaming through the application process.
- Orphaned files are possible after a partial failure; deletion is resumable and
  a reconciliation routine exists.
- The mode must be re-asserted on write, not only on creation: `mkdir(parents=True)`
  recreates a missing ancestor with the process umask, which once left the root
  `0o755`.
