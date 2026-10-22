# ADR 0004: Bounded in-process worker instead of a distributed queue

- Status: Accepted
- Date: 2026-10-22

## Context
Document parsing must not run inside the request cycle, but MAFHUM has a zero
budget and must run on ordinary hardware. Celery, RQ or SQS each add a broker,
extra processes and operational surface before any scale has been demonstrated.

## Decision
Run a **bounded in-process worker pool** (`PROCESSING_WORKER_COUNT`, default 2)
that claims jobs from PostgreSQL with `SELECT ... FOR UPDATE SKIP LOCKED`. Jobs
carry a timeout and an attempt cap; stale jobs are recovered on startup. Retries
are keyed on `(document_id, generation)`, so segments are replaced atomically and
a retry cannot create duplicates.

## Consequences
- No broker to install, secure or monitor; the database is the only coordinator.
- Job claiming is safe against concurrent workers at the *database* level, so
  `SKIP LOCKED` does work across processes.
- **Quota enforcement is process-local.** Document counts, byte totals and the
  login throttle are checked in-process, so running N replicas allows roughly N×
  the intended volume. This is documented in `docs/DATA_POLICY.md` and
  `docs/THREAT_MODEL.md` rather than described as distributed rate limiting.
- Supported topology today is **one API process**. Scaling out requires moving
  quota checks into the database (atomic conditional inserts or advisory locks)
  first — that is the precondition, not a follow-up.
