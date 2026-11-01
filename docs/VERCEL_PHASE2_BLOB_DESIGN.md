# Vercel Blob Phase 2 — Private Storage Design

**Status:** Design verified; implementation and acceptance tests not yet complete.  
**Branch:** `feat/vercel-native-migration`  
**Database decision:** Neon PostgreSQL selected by the owner; provisioning and credentials remain gated on separate approval.  
**Scope:** Code and test design only. No Blob store, Neon project, paid resource, secret, production setting, or real-user data is created or changed.

## 1. Provider compatibility verified

Vercel documents a first-party Python SDK distributed as `vercel` (install with `pip install vercel`). The Blob Python API exposes:

- `vercel.blob.AsyncBlobClient.put(...)`, accepting an `AsyncIterator[bytes]` body and `access="private"`.
- `AsyncBlobClient.get(...)`, returning a streamed async iterator for the object body.
- `BlobClient` synchronous operations, but the documented synchronous upload examples use an in-memory `bytes` body.

Private Blob is the only acceptable store mode for MAFHUM documents. Private reads require authentication and must remain behind MAFHUM's own authorization checks. Public Blob URLs are not acceptable for original documents, extracted source files, or generated user presentations.

Official references:
- https://vercel.com/docs/vercel-blob/using-blob-sdk
- https://vercel.com/docs/vercel-blob/private-storage
- https://vercel.com/changelog/vercel-private-blob-is-now-generally-available

## 2. Existing contract that must be preserved

The current `DocumentStorage` protocol in `apps/api/src/mafhum_api/services/documents/storage.py` is synchronous:

- `generate_key()`
- `write(key, chunks, max_bytes)`
- `open_read(key)`
- `exists(key)`
- `stat(key)`
- `delete(key)`
- `delete_many(keys)`
- `reconcile_orphans(live_keys)`

Document ingestion, retry, deletion, account reclamation, export, and presentation generation depend on this contract. Local storage must remain the default for development/tests. Existing authorization stays in the service layer and SQL ownership predicates; a Blob pathname or URL is never authorization.

## 3. Critical implementation constraint

The Python SDK's streaming upload API is asynchronous, while the current storage protocol is synchronous. The synchronous SDK path is documented with a `bytes` body and would require buffering the complete upload in memory. That is not an acceptable implementation for the current 20 MB upload contract or future multipart uploads.

Before coding the adapter, choose and test one of these approaches:

1. Refactor the storage protocol and all call sites to an async streaming contract, with a carefully scoped migration that preserves synchronous CPU-bound document parsing and worker behavior; or
2. Implement a bounded sync/async bridge that streams chunks through `AsyncBlobClient` without collecting the complete file in memory, with explicit cancellation, exception propagation, iterator closure, and thread-safety tests.

Do not ship a whole-file `bytes` buffer as the default adapter. Do not write uploads to a temporary file and describe that as durable storage; temporary disk may only be used as a bounded transient bridge if its lifecycle and limits are tested.

## 4. Required object-key and metadata rules

- Generate opaque, server-side keys with at least 256 bits of entropy; never include a user filename, user ID, or caller-supplied pathname in a storage key.
- Use a deterministic private Blob pathname derived only from the opaque key, e.g. `mafhum/<shard>/<opaque-id>`.
- Set `access="private"` explicitly on every operation that supports it.
- Store the Blob pathname/object identifier in PostgreSQL, not a public URL.
- Verify returned pathname, size, content type, and digest before the database row can be committed as a successfully stored object.
- Keep the current maximum upload byte enforcement while consuming the stream; reject immediately when the byte budget is exceeded.
- Prevent overwrites; key collisions must fail closed.
- Keep download authorization in MAFHUM before calling private Blob `get()`; never accept a Blob URL from the client.
- Deletion must be idempotent and distinguish an already-absent object from a failed deletion.
- Orphan reconciliation must be implemented using paginated Blob listing and report-only semantics first; automatic deletion is a separate explicit operation.
- Private document responses must retain `Cache-Control: private, no-store`.

## 5. Upload flow and the 4.5 MB Function request limit

The current API accepts multipart uploads through FastAPI, but Vercel Functions impose a 4.5 MB request-body limit. The existing 20 MB application limit cannot be met by proxying the file through the API Function.

The required future flow is:

1. Authenticate the user and validate CSRF.
2. Create a short-lived upload intent in PostgreSQL, bound to owner, document ID, opaque Blob pathname, expected maximum bytes, allowed media type, and expiry.
3. Issue a narrowly scoped client upload token or equivalent SDK-supported upload authorization; never expose the store read-write token.
4. Upload directly to a **private** Blob store using the supported client-upload flow.
5. Finalize through the API: verify the intent, Blob object metadata, actual size, content type, ownership, and digest; then perform the real malware scan before the object is eligible for processing.
6. Keep the object quarantined and unavailable if verification or scanning fails; clean up abandoned intents/objects through a bounded reconciler.

The exact Python SDK/client-upload API and token policy must be proven before implementation. Do not invent a token format or rely on public upload URLs.

## 6. Required tests before Phase 2 can be called complete

- Contract tests run against both LocalFileStorage and VercelBlobStorage.
- Streamed upload with chunk boundaries, empty chunks, exact-limit and over-limit cases.
- No whole-file buffering for uploads or downloads.
- Private access is explicit; public access is never requested.
- Missing/invalid token, missing object, transient SDK failure, timeout, cancellation, and malformed SDK response fail safely.
- Key traversal, path injection, collision, and overwrite rejection.
- Digest and size verification; metadata mismatch rejection.
- Authenticated cross-user download denial before Blob access.
- Idempotent delete, delete-many partial failure, and report-only orphan reconciliation.
- Upload intent expiry, replay prevention, ownership mismatch, and abandoned-upload cleanup.
- Malware scan still occurs before processing; scanner outage/timeout remains fail-closed when scanning is required.
- Existing document, deletion, export, presentation, and worker tests remain green.
- No production data migration is included in this phase.

## 7. Explicit non-actions

This design commit does not create a Blob store, Neon database, Vercel environment variable, token, or deployment. It does not modify `main`, merge PR #12 or PR #13, promote a preview, or migrate any existing file. Provisioning, credentials, production configuration, and data migration require separate owner approval.
