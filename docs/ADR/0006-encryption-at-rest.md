# ADR 0006 — Encryption at rest: design and testable interfaces

- **Status:** Proposed — **design and interfaces only. NOT enabled in any environment.**
- **Date:** 2026-10-28
- **Deciders:** repository owner (key-management choice is still open)
- **Related:** `docs/THREAT_MODEL.md`, `docs/DATA_POLICY.md`, `docs/SECURITY_ASSESSMENTS.md`, `deploy/docker-compose.prod.yml`

## Honest summary for a non-specialist

MAFHUM does **not** currently encrypt user content at the database-field level.
Passwords are hashed, session tokens are stored as digests, uploaded blobs get
`0600` file permissions and the production blueprint keeps them off any public
path — but a person who obtains the PostgreSQL data files, a database dump, or
the blob volume can read the raw content and the audit metadata. There is no
volume/disk encryption in the Compose template either; that is a host-level
control the operator must provide.

This ADR records the design MAFHUM would adopt, and ships a tested reference
implementation of its cryptographic core using **synthetic test keys only**.
No production key, KMS client, encrypted column, migration, or runtime call
site exists. Passing the crypto tests proves the *primitive*, not that the
product encrypts anything.

## Context

### What is stored

| Data | Current protection | Residual exposure |
|---|---|---|
| Uploaded PDF/DOCX and generated PPTX blobs | `0600` files on a named Docker volume | Host/volume/dump reader sees bytes |
| Extracted text segments, sources, study data | PostgreSQL row data | DB dump or data-file reader sees text |
| Audit events | Allow-listed, bounded JSON metadata | DB dump reader sees actor ids/timestamps |
| Password hashes | scrypt with per-user salt | Not reversible, still sensitive metadata |
| Session tokens | SHA-256 digest only | Digest is not a usable token |

### Threats this design addresses

- T1 — An attacker exfiltrates a database dump or the `pgdata` volume.
- T2 — An attacker copies the blob storage volume.
- T3 — A ciphertext from one row is replayed into another row (swap attack).
- T4 — A key must be rotated without a long, blocking rewrite of every row.

### Threats it does NOT address

- A fully compromised API process (it must be able to decrypt to serve reads).
- A compromised or malicious KMS administrator.
- Data in use, in memory, in logs, or in application-level audit details.
- Backups: a backup of ciphertext is only as safe as its own key handling.
- Side channels, traffic analysis, or metadata (row counts, sizes, timestamps).

## Decision

### 1. Two layers, with different owners

- **Volume / disk encryption** (LUKS, cloud-provider encrypted volumes, or
  PostgreSQL TDE) is an **operator/host responsibility**, not application code.
  It protects against physical media loss and is the cheapest mitigation for
  T1/T2. MAFHUM's Compose template cannot enable it.
- **Application-layer field encryption** is the second layer: selected sensitive
  columns are encrypted before they reach SQL. It is what limits a database
  dump to ciphertext.

Both are needed; neither alone is a complete answer.

### 2. Envelope format and AEAD with associated data

Use AES-256-GCM with a self-describing token:

```
b"MFE1" | key_id_len | key_id | nonce(12) | AESGCM(plaintext, aad)[ct|tag]
```

`aad = aad_for(table, column, row_id)` binds a ciphertext to exactly one cell of
one row. AES-GCM authenticates the AAD but does not store it, so T3 is detected
at `decrypt()` rather than by convention. The `key_id` is a non-secret label —
not key material — so rotation does not require guessing which key to try.

The reference implementation is `apps/api/src/mafhum_api/core/crypto.py`;
`apps/api/tests/test_field_encryption.py` verifies round-trip, tamper
detection, wrong-AAD rejection, unknown-key rejection, and rotation. All keys
in those tests are generated in memory at test time.

### 3. Key hierarchy

```
KMS / HSM master key (owner-controlled, never in this repository)
        |
        v  wraps / unwraps
Data encryption keys (DEKs), one active + N previous, labelled by key_id
        |
        v  AES-256-GCM
Per-cell ciphertext + authenticated AAD
```

The application never stores a DEK in an image, in `Settings`, in an
environment variable checked into a template, or in the database it encrypts.
A production implementation would fetch wrapped DEKs at startup from the
owner's KMS and hold only the unwrapped key in process memory.

### 4. Rotation procedure

1. Add the new DEK as the **first** key in the encryptor's key list (new primary).
2. Keep the old DEKs registered for a **dual-read window**. Reads decrypt with
   whichever key the token names; writes always use the primary.
3. Run a bounded, resumable background job that calls `rotate(token, aad=aad)`
   for each ciphertext still on an old key — batches with a fixed size, its own
   transaction per batch, and progress recorded so an interruption resumes.
4. Verify zero remaining old-key tokens, then retire the old DEK from the KMS.
5. Revoke/retire the old wrapped DEK and record the rotation in the audit trail.

`rotate()` is idempotent with respect to the key id, but produces a fresh nonce
on every call, so ciphertext bytes differ — expected and safe.

### 5. What must be decided before any implementation

These are **owner decisions**, not coding tasks:

- KMS/HSM provider, region, and who holds the root credential.
- Which columns are in scope (extracted text and source notes are the obvious
  candidates; encrypting indexed/searchable columns changes query design).
- Retention of encrypted backups and of retired keys: deleting a key deletes
  the ability to read old backups.
- Legal hold and data-subject erasure behaviour when only ciphertext remains.
- Whether blobs (PDF/DOCX/PPTX) are encrypted at the storage layer or through
  an object store's SSE — a separate decision with different key handling.
- Rotation cadence, dual-read window length, and the alert that fires when a
  token still uses a retired key.
- A migration plan that does not hold a long table lock on a live database.

### 6. Deliberate non-goals of this change

- No `MAFHUM_FIELD_KEYS` environment variable, no key file, no default key.
- No encrypted SQLAlchemy `TypeDecorator`, no model change, no migration.
- No change to blob storage, backups, or the audit table.
- No claim that "encryption at rest is implemented" anywhere in the product
  documentation.

A future change that wires any of this in MUST update this ADR's status from
*Proposed* to *Accepted*, list the owner decision that authorised it, and add an
integration test against the real KMS interface (not a synthetic key).

## Consequences

**Positive**

- The cryptographic core and its failure modes are testable today, before a
  KMS is chosen.
- AAD binding is designed in from the start; retrofitting it after ciphertext
  exists would require re-encrypting everything.
- Rotation is a background job rather than a maintenance window.
- The repository contains no production key material to leak.

**Negative / costs**

- One AES-GCM operation per read and per write of an encrypted column.
- Encrypted columns cannot be indexed or searched with SQL predicates; features
  that filter on them must be redesigned.
- Ciphertext is larger than plaintext (header, nonce, tag, base64).
- Key availability becomes a hard runtime dependency: losing the KMS means
  losing the data. Backups and disaster recovery must include key escrow.
- A partially rotated database must be supported indefinitely until the job
  finishes.

## Verification performed

`apps/api/tests/test_field_encryption.py` (synthetic in-memory keys only):

- round-trip with an AAD bound to `(table, column, row_id)`;
- tampered ciphertext, truncated token, garbage token → `DecryptionError`;
- correct ciphertext with a different row/table AAD → `DecryptionError`;
- unknown `key_id` → `DecryptionError`;
- dual-read rotation: old key decrypts, `rotate()` re-encrypts under the new
  primary, an encryptor with only the new key can read the result, an encryptor
  with only the old key cannot;
- a guard test asserting that no encryption key setting exists in `Settings`
  and that `main.py` does not construct an encryptor.

**Not verified:** any real KMS integration, any encrypted production column,
volume encryption on a real host, backup/restore of encrypted data, performance
under load, or key-escrow/disaster-recovery procedures.
