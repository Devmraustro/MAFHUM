# MAFHUM — Deployment

## Process model: one document worker per database

The document processing worker holds a PostgreSQL advisory lock for its lifetime.
The first process to start a worker acquires it; a second process attempting to
start one **fails at startup** with `WorkerAlreadyRunningError`.

This is enforced in software, not by convention. Job claiming was already safe
across processes (`SELECT ... FOR UPDATE SKIP LOCKED` plus a `queued -> running`
transition inside the claim transaction, with heartbeats refreshed during
processing), so duplicate execution was never the risk. What is unsupported is
running several workers when stale-job recovery, thread-pool sizing and failure
accounting were all designed for one.

### Scaling out

Start exactly one instance with the worker enabled, and disable it everywhere else:

```bash
# the single processing instance
PROCESSING_WORKER_ENABLED=true   # default

# every other replica
PROCESSING_WORKER_ENABLED=false
```

Replicas still serve all HTTP traffic, including uploads. Only document processing
is confined to the worker instance. Quotas and the login throttle are
database-enforced and safe across any number of replicas.

### Why an advisory lock

It cannot be left behind. PostgreSQL releases a session-scoped advisory lock when
the holding connection closes, including when the process is killed. A lock file or
a `held_by` column survives a crash and blocks every future startup until someone
cleans it up by hand. No additional service is required — the PostgreSQL the
application already needs expresses the constraint.

The lock is held on a dedicated non-pooled connection. Borrowing a pooled session
would release the lock as soon as that session returned to the pool, which would
silently defeat the mechanism while appearing to work.

### Non-PostgreSQL databases

SQLite has no advisory locks and is used only for fast unit tests. On that dialect
the lease is **not enforced** and a warning is logged
(`event: worker_lease_unenforced`). Production requires PostgreSQL.

## Migrations

The entrypoint runs `alembic upgrade head` before serving. With multiple replicas,
run migrations on only one:

```bash
MAFHUM_RUN_MIGRATIONS=false   # read by the entrypoint, not by the app; on every replica except the one that migrates
```

## Production configuration

`assert_production_ready()` runs at startup and refuses to serve when
`app_env=production` unless `SESSION_SECRET` is set, `COOKIE_SECURE` is true, and
the database URL is not localhost. Configure these before starting.

## Malware scanning

No engine is bundled. To enable scanning, run `clamd` and point the application at
it with `CLAMD_UNIX_PATH` or `CLAMD_HOST`. Set
`MALWARE_SCAN_REQUIRED=true` to reject uploads unless the scanner returns a
clean verdict — with that flag, an unavailable or erroring scanner rejects uploads
rather than admitting them.

Until a real engine has been verified with the EICAR test file, do not claim
malware protection. `GET /api/v1/documents/limits` reports the live
`malware_scanning_available` state.
