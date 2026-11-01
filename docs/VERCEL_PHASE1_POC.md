# MAFHUM — Phase 1: Vercel-Native FastAPI Proof of Concept

**Branch:** `feat/vercel-native-migration` (baseline `11ccaed`, PR #12 head; PR #12 untouched)
**Scope:** Phase 1 of `VERCEL_NATIVE_MIGRATION_PLAN.md` only. No Blob migration,
no queue/worker migration, no production changes.

## What was implemented

| File | Change |
|---|---|
| `apps/api/src/mafhum_api/vercel_app.py` | New serverless entrypoint module: `create_vercel_app()` = the existing `create_app(settings, start_worker=False)` plus a fail-fast PostgreSQL-only guard. Exposes the module-level `app` Vercel loads. |
| `apps/api/api/index.py` | New Vercel-recognized entrypoint shim: makes the `src/` layout importable and re-exports `mafhum_api.vercel_app.app`. No logic. |
| `apps/api/vercel.json` | New project config for a Vercel project rooted at `apps/api`: pins the single function `api/index.py` and excludes tests/tools/alembic/data/docs from the bundle. |
| `apps/api/.python-version` | New: `3.12` (Vercel supports 3.12/3.13/3.14; the codebase requires `>=3.11`). |
| `apps/api/pyproject.toml` | `[tool.vercel] entrypoint = "api.index:app"` (the documented explicit entrypoint mechanism). Runtime dependencies were already pinned in `[project].dependencies` — the Vercel build installs exactly those, not the dev extras. |
| `apps/api/src/mafhum_api/config.py` | New setting `db_pool_pre_ping` (default `false` — existing deployments unchanged). |
| `apps/api/src/mafhum_api/db/base.py` | `Database(..., pool_pre_ping=...)` passed to the engine (PostgreSQL pools only). |
| `apps/api/src/mafhum_api/main.py` | Wires the setting through `build_context`. |
| `apps/api/src/mafhum_api/api/v1/health.py` | Honesty fix: `processing_worker` now reports `ok` only when the dispatcher thread is actually running (`worker.is_running`), never from the mere existence of a `worker_id`. A replica or serverless instance without a live worker now reports `stopped` instead of a fabricated `ok`. |
| `apps/api/tests/test_vercel_deployment.py` | 19 deployment-specific regression tests (below). |

## Serverless contract (and how each requirement is met)

1. **No persistent threads at cold start.** `start_worker=False` is a code-level
   guarantee in `create_vercel_app()`, independent of the
   `PROCESSING_WORKER_ENABLED` environment variable. Document processing moves
   to durable jobs only in Phase 3.
2. **PostgreSQL-only.** `assert_postgres_only()` aborts the cold start for any
   `DATABASE_URL` that is not `postgresql://` / `postgresql+psycopg://`. SQLite
   is never a fallback (the `Database` layer keeps its library-level behaviour
   for unit tests; the deployment path does not).
3. **Bounded connection lifecycle.** The pool keeps the operator-supplied
   `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` bounds; `DB_POOL_PRE_PING=true` replaces
   connections that went stale between invocations; the FastAPI lifespan
   disposes the engine on shutdown (Vercel allows 500 ms of teardown after
   SIGTERM — pool disposal fits comfortably).
4. **No migrations at Function startup.** Nothing in the entrypoint imports or
   invokes Alembic; the lifespan issues no DDL (regression-tested with a
   statement listener). Schema changes remain a controlled release step.
5. **Security controls unchanged.** Same factory, same middleware, same
   dependencies: security headers, session cookies, the Origin allowlist and
   the CSRF double-submit are regression-tested through the Vercel wiring.
   Readiness reports the honest serverless state (`processing_worker:
   stopped` → `degraded`), because without Phase 3 that deployment cannot
   process documents — and must say so.
6. **Next.js frontend untouched.** `apps/web` (its `next.config.mjs`
   same-origin `/api` proxy and its `vercel.json`) is byte-identical to the
   baseline; the existing `mafhum-web` Vercel project (root directory
   `apps/web`) is unaffected by the new files under `apps/api`.

## Deployment-specific regression tests (19, hermetic)

Entrypoint shim re-exports the app and inserts `src/`; project config pins the
entrypoint and trims the bundle; the app is the standard app with the worker
disabled; cold start starts no worker threads even when the env var allows it;
non-PostgreSQL URLs abort (sqlite/mysql/empty); `postgresql://` accepted; pool
bounds and pre-ping reach the engine; default settings keep the container pool
behaviour; the lifespan disposes the engine on shutdown; cold start issues no
DDL; entrypoint sources import no Alembic; health endpoints report the honest
serverless state; register/login + CSRF double-submit + Origin allowlist work
through the Vercel wiring; security headers present; unauthenticated requests
rejected; the Next.js proxy config is unchanged.

## Vercel conventions verified (official docs, 2026-10)

- Entrypoint detection: an `app` instance in `index.py` under `api/` — also
  pinned explicitly via `tool.vercel.entrypoint`.
- Python 3.12 default (3.13/3.14 available); pinned with `.python-version`.
- Dependencies from `pyproject.toml` `[project].dependencies`.
- Single Vercel Function serves the whole app (FastAPI router matching);
  Fluid compute by default; lifespan startup/shutdown honored; shutdown
  teardown limited to 500 ms after SIGTERM.
- Python functions are not tree-shaken: everything reachable is bundled
  (500 MB limit) — hence `excludeFiles` for tests/tools/alembic/data.

## Live preview deployment (evidence, 2026-11-01)

An isolated throwaway project — **`mafhum-api-poc`** (team
`devmraustros-projects`, Hobby plan, root directory `apps/api`, framework
`python`, **not** git-linked so nothing auto-deploys) — was created via the
Vercel API purely to prove the build. The real project `mafhum-web` and its
environment were not touched.

- **Deployment:** `dpl_Hw5syp9hJyUBfegg5VqznLgPA278` — status **READY**
  (preview environment), built from git SHA `eed6d86` (PR #13 head).
  URL: `mafhum-api-l8fvp4qww-devmraustros-projects.vercel.app`;
  branch alias: `mafhum-api-poc-git-feat-vercel-nat-6c66a0-devmraustros-projects.vercel.app`.
- **Build log confirms the conventions:** clone of `feat/vercel-native-migration`
  @ `eed6d86` → “Using Python 3.12 from `apps/api/.python-version`” → uv
  installs from `pyproject.toml` → “Compiling Python bytecode…” → “Build
  Completed in /vercel/output [15s]” → one Python Function
  (`lambdaRuntimeStats: {"python":1}`), build cache ~48 MB. This is exactly
  the zero-config Python path the official docs describe.
- **Preview environment variables** (encrypted, preview + production
  targets): `APP_ENV=development`, `STORAGE_ROOT=/tmp/mafhum-storage`,
  `DB_POOL_SIZE=1`, `DB_MAX_OVERFLOW=1`, `DB_POOL_PRE_PING=true`,
  `PROCESSING_WORKER_ENABLED=false`, `LOG_FORMAT=json`. Deliberately **no
  `DATABASE_URL`**: wiring a managed PostgreSQL is an owner decision (Phase 2+),
  so DB-backed endpoints cannot work in this preview and `/health/ready`
  reports `degraded` (database unavailable, processing worker stopped) —
  the honest fail-closed state.
- **What is *not* verified at runtime:** the account’s deployment protection
  (“Vercel Authentication”, `all_except_custom_domains`) requires a logged-in
  team member for every `*.vercel.app` URL — anonymous probes redirect to
  `vercel.com/login`. This is correct security posture and was **not**
  disabled. A logged-in owner opening the preview URL should see
  `/health/live` → `ok` and `/health/ready` → `degraded` (no DB configured).
  Runtime behaviour is otherwise covered by the hermetic test suite, which
  runs the identical factory against a real PostgreSQL instance.
- **Operational notes:** the API-created *first* deployment of a brand-new
  project is auto-promoted to the project’s default (production) environment;
  that one was built before the environment variables covered it (default
  `STORAGE_ROOT` is not writable on Vercel) and was deleted — the preview
  above is the project’s only deployment. The Vercel REST API also rejects a
  first deployment for a framework-less project unless `projectSettings` is
  supplied (the connector cannot send it); resolved by setting the project’s
  framework to `python`. Preview deployments on this project expire after
  30 days, and the whole project can be deleted by the owner at any time.

## Known Phase-1 boundaries (honest, not blockers for the POC)

- **No managed database is wired.** A real deployment needs a managed
  PostgreSQL (e.g. the Neon integration) — an owner decision in Phase 2+.
  Database connectivity is proven at the code level (real PostgreSQL in the
  test suite), not against a live managed instance.
- **`/health/ready` on a serverless deployment reports `degraded`** until
  Phase 3 (no in-process worker) and Phase 4 (no ClamAV engine) land. This is
  the correct fail-closed disclosure, not a defect.
- **Uploads still transit the request body** (Vercel Function body limit
  4.5 MB vs the app's 20 MB limit) — direct-to-Blob upload is Phase 2.
- **Local filesystem storage is ephemeral** on Vercel — `/tmp` works for a
  POC, real private storage is Phase 2 (Vercel Blob).
