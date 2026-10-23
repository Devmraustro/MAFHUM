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
- **Superseded (2026-10-23):** this ADR originally stated that quota enforcement
  was process-local, so N replicas allowed roughly N× the intended volume. That
  was tested rather than assumed, and the claim was **wrong for document quotas**:
  `_enforce_quota` locks the owning user row with `SELECT … FOR UPDATE` and reads
  counts and byte totals from the database, so it already held across processes.
  `tests/test_multi_instance_state.py` builds two independent application
  instances over one database and confirms the per-user document cap and byte cap
  both hold across them.
- The claim **was** correct for the login throttle, which counted attempts in a
  module-level dictionary. That has been moved into the database
  (`login_attempts`, migration `0006_login_throttle`), so the configured attempt
  limit is now the real limit regardless of how many API processes serve traffic.
- Remaining single-process constraints are unchanged and still real: the worker
  pool runs inside the API process, so a replica without a worker does not process
  jobs, and a pathological parser cannot be forcibly terminated (see above).
- The lesson recorded here is the reason for the correction: a documented
  limitation that was never tested can be as misleading as an undocumented one,
  because it is trusted.
