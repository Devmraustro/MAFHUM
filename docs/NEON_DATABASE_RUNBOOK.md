# Neon PostgreSQL — Integration Runbook (Phase C)

**Status:** code-side complete and verified against real PostgreSQL. Real
Neon acceptance is **BLOCKED — OWNER ACTION REQUIRED**: this session may not
create paid resources or handle production credentials.

MAFHUM is PostgreSQL-only by design (`assert_postgres_only` in
`mafhum_api.vercel_app` aborts the cold start for any non-PostgreSQL
`DATABASE_URL`). Neon is the owner-selected managed provider.

## 1. Required environment variables (exact names)

| Variable | Value for Neon | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://<user>:<password>@<host>/<db>?sslmode=require` | Use the **pooled** (`-pooler`) endpoint for serverless Functions; the direct endpoint for migrations. `sslmode=require` is mandatory — Neon refuses plaintext. |
| `DB_POOL_SIZE` | `1`–`2` per Function instance | Each serverless instance gets its own pool; keep the product bounded. |
| `DB_MAX_OVERFLOW` | `1` | Bounded overflow only. |
| `DB_POOL_PRE_PING` | `true` | Replaces connections that Neon closed between invocations. |
| `DB_POOL_RECYCLE_SECONDS` | `240` | Just below Neon's idle connection limit; pair with pre-ping. |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10` | Default; bounds cold-start latency when unreachable. |

These are real `Settings` fields (`apps/api/src/mafhum_api/config.py`);
`.env.example` documents all of them, and the config contract test enforces
that the template never drifts from the model.

## 2. What the application already guarantees (verified)

- Bounded pool with operator-supplied size/overflow; pre-ping and recycle are
  wired to the engine (regression-tested in
  `tests/test_vercel_deployment.py`).
- `unit_of_work()` gives commit/rollback/close semantics on every transaction;
  failures roll back and surface as handled errors, never partial writes.
- Migrations are a **controlled release step**: nothing in the Function
  startup path runs Alembic or DDL (regression-tested with a statement
  listener).
- Health endpoints report database status without leaking credentials.
- Fail-fast: unreachable databases abort within the connect timeout instead
  of hanging requests.

Verified against real PostgreSQL (pgserver locally, `postgres:16` service in
CI): full backend suite (1052 passed / 7 environmental skips at the time of
writing) and the `alembic upgrade head → downgrade base → upgrade head`
round-trip.

## 3. Owner setup steps (not performed by this session)

1. Create the Neon project/database (owner decision; paid implications are
   the owner's to accept).
2. Create a **branch** for staging/preview if desired; keep production on a
   protected branch.
3. Set the environment variables above on the API deployment (Vercel project
   env or container env). Never commit them.
4. Run migrations once per environment as a release step:
   ```bash
   DATABASE_URL="postgresql+psycopg://…direct-host…/mafhum?sslmode=require" \
     alembic upgrade head
   ```
   Use the **direct** (non-pooler) endpoint for migrations; DDL through the
   pooler is not supported by Neon.
5. Verify: `GET /health/ready` → `{"status": "degraded"|"ok", "checks":
   {"database": "ok", …}}` — the database check must be `ok`.

## 4. Migration execution runbook

- **When:** as an explicit release step, after the code that requires the new
  schema is built but before it serves traffic; never from Function startup.
- **Upgrade:** `alembic upgrade head` (direct endpoint).
- **Rollback:** `alembic downgrade -1` (one revision) or a previously agreed
  target; test the downgrade path in CI — it is part of the pipeline
  ("Migrations — upgrade and downgrade" job) and locally.
- **Zero-downtime note:** deploy code compatible with both old and new schema
  first (additive migrations), run the migration, then deploy the code that
  uses it. Destructive migrations require a backup first (§6).

## 5. Acceptance checklist (run after owner provisioning)

- [ ] `DATABASE_URL` uses `sslmode=require`; connection without TLS fails.
- [ ] `/health/ready` reports `database: ok`.
- [ ] Register/login/upload/query round-trip works end-to-end.
- [ ] Pool bounds respected under concurrent requests (no connection storm;
      watch Neon's connection metrics).
- [ ] Pre-ping + recycle survive an idle period > recycle window.
- [ ] `alembic upgrade head` + `alembic downgrade base` + re-upgrade on a
      **scratch Neon branch** (not production).
- [ ] Failover/idle-suspend behavior: Neon suspends idle computes — first
      request after resume should succeed (cold start, not an error storm).

## 6. Backup and restore

- Neon provides point-in-time restore and branching; the owner should confirm
  the retention window for their plan and document the restore procedure in
  their incident runbook.
- Before any destructive migration: create a Neon branch (snapshot) and record
  its ID in the change ticket.

## 7. Known limitations

- Neon's pooled endpoint (PgBouncer, transaction mode) does not support some
  session-level features; MAFHUM's usage (connection-per-transaction,
  `SKIP LOCKED` row locking) is compatible, but **advisory locks used by the
  processing worker must use the direct endpoint** — a serverless-deployed
  worker is not part of this phase regardless (see the durable-processing
  design doc).
- Serverless + managed Postgres means N pools for N concurrent instances;
  the bounded sizes above keep the product within Neon's connection limits.
