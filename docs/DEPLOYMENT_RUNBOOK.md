# MAFHUM deployment runbook

Date: 2026-10-27 · Applies to: `main` after PR #2 (Phases 0–6) plus Phase 7.

This is an operator procedure, not a claim that production is running. Every
command below is either (a) already exercised in CI, or (b) explicitly marked
**operator action**. Nothing here requires a paid service.

---

## 1. What gets deployed

| Component | Artefact | Verified where |
|---|---|---|
| PostgreSQL 16 | `postgres:16` (Compose) | Migrations job + deployment rehearsal |
| FastAPI API | `apps/api/Dockerfile` | Gate B (build/non-root/readiness/shutdown) + deployment rehearsal |
| Next.js web | `apps/web/Dockerfile` (Phase 7) | Local runtime-layout check + deployment rehearsal |
| Vercel web (optional) | `apps/web/vercel.json` | See §7 — a frontend-only rehearsal |

The web tier never talks to a public API host from the browser. It proxies
`/api/:path*` and `/health/:path*` server-side to `MAFHUM_API_URL`
(`apps/web/next.config.mjs`). That variable is **not** `NEXT_PUBLIC_`; it is a
server-side rewrite target.

---

## 2. Prerequisites

- Docker with Compose v2 (for the self-hosted stack), or a Node 22 runtime for
  the web image.
- PostgreSQL 16 reachable from the API container.
- Python 3.11 with `pip install -e "apps/api[dev]"` for migrations/tools.
- A private `STORAGE_ROOT` volume. **Backing up the database alone is not a
  backup** — blobs live outside it (`docs/DATA_POLICY.md`).

Required environment (no prefix; see `.env.example` and
`apps/api/tests/test_config_contract.py`):

```
APP_ENV=production
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/mafhum
SESSION_SECRET=<random 48+ bytes>          # assert_production_ready() requires it
COOKIE_SECURE=true
ALLOWED_ORIGINS=["https://<web-host>"]
STORAGE_ROOT=/var/lib/mafhum/storage
PROCESSING_WORKER_ENABLED=true              # exactly ONE replica
MAFHUM_RUN_MIGRATIONS=true                  # exactly ONE replica / one-shot job
```

Optional: `CLAMD_HOST` / `CLAMD_UNIX_PATH` + `MALWARE_SCAN_REQUIRED=true` **only
after** a real engine has answered the EICAR probe. Do not claim scanning before
that. `RESEARCH_RETRIEVAL_ENABLED=false` by default; enabling it permits
outbound fetches that must be reviewed against `docs/SSRF_REVIEW.md`.

---

## 3. Pre-deploy checklist

1. **Capture a backup** before any schema change:
   ```bash
   # Quiesce the writer first, then archive DB + blobs together.
   docker compose stop api
   python apps/api/tools/backup.py \
     --database-url "$MAFHUM_DATABASE_URL" \
     --storage-root /var/lib/mafhum/storage \
     --out ./backups --require-quiesced --verify
   ```
   `--verify` restores into a scratch database and checks every `storage_key`
   against its recorded SHA-256. A backup that has not been restored is not
   evidence.
2. Confirm `alembic upgrade head` succeeds on a copy of production data
   (`0001` → `0009_admin_audit` is linear).
3. Record the currently deployed image tag / commit SHA — rollback needs it.
4. Confirm `SESSION_SECRET` and `COOKIE_SECURE` are set; the entrypoint refuses
   to start production otherwise.

---

## 4. Deploy the Compose stack

```bash
export POSTGRES_PASSWORD='<from secrets manager>'
export SESSION_SECRET='<from secrets manager>'
docker compose up -d --build --wait
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/
curl -fsS http://127.0.0.1:3000/health/live   # proves the Next rewrite
python tools/e2e/compose_smoke.py              # real register/upload/process/download
```

Only one API replica may run migrations and the worker:

```bash
MAFHUM_RUN_MIGRATIONS=false PROCESSING_WORKER_ENABLED=false docker compose up -d --scale api=3
```

Quotas and the login throttle are database-enforced and safe across replicas;
the bounded document worker is not shared (`docs/DEPLOYMENT.md`).

---

## 5. Verify after deploy

- `/health/ready` reports `database=ok`, `storage=ok`, `processing_worker=ok`.
- `python tools/e2e/compose_smoke.py` prints `ALL COMPOSE SMOKE CHECKS PASSED`.
- `docker compose ps` shows all three services healthy.
- An admin can read `GET /api/v1/admin/overview` and
  `GET /api/v1/admin/metrics`; a normal user gets `404 resource_not_found`.
- `GET /api/v1/documents/limits` reports the true
  `malware_scanning_available` state — do not assert "clean" without an engine.

---

## 6. Monitoring and administration

Roles are granted **only** from the operator's command line:

```bash
DATABASE_URL='postgresql://...' python apps/api/tools/set_admin.py \
  --email operator@example.com --role admin
python apps/api/tools/set_admin.py --list
```

There is deliberately no HTTP role-change endpoint. Every grant writes an
`admin_role_changed` audit row.

Admin endpoints (all read-only, cookie-authenticated, CSRF-free because they are
GETs):

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/admin/overview` | Counters: users, documents by status, decks by status, audit/denied events |
| `GET /api/v1/admin/usage` | Per-account aggregate usage (id, email, counts) — paginated |
| `GET /api/v1/admin/audit-events` | Append-only trail; filter by `action`/`outcome` |
| `GET /api/v1/admin/metrics` | Redacted Prometheus-style counters (no PII) |

**Honest limit:** no external alerting, log aggregation, uptime probe or
dashboard is configured. The metrics endpoint is a scrape source, not a
monitoring system. Wiring it to a real alertmanager and retaining the redacted
`app_env=production` JSON logs is an operator action that remains open.

---

## 7. Vercel web rehearsal

`apps/web/vercel.json` pins the framework and install/build commands and fails
the build if `MAFHUM_API_URL` is missing. **This is a build-time variable**:
`next.config.mjs` evaluates `rewrites()` during `next build` and writes the
destination into `.next/routes-manifest.json`. Setting it only at runtime would
silently proxy to `127.0.0.1:8000`, which is the web container itself. The
Compose build passes it as `build.args.MAFHUM_API_URL=http://api:8000`.

Point the Vercel project's root directory at `apps/web`, set `MAFHUM_API_URL` to
the **public API origin** as a build environment variable, and deploy. Because the API is not deployed on Vercel,
a frontend-only deployment will serve the UI but every `/api` call will fail —
that is a build/deploy rehearsal, not a working full-stack product, and must be
described as such. See `docs/STATUS_REPORT_2026-10-27.md` §12 for the recorded
result of the rehearsal performed on 2026-10-27.

---

## 8. Rollback and restore

See [`docs/ROLLBACK_GUIDE.md`](ROLLBACK_GUIDE.md). The short version: stop
traffic, restore the pre-deploy backup (database + blobs together), downgrade
the schema one revision at a time, redeploy the previous image tag, re-run the
smoke journey, and record what happened. Never roll forward a migration that
has not been restored from a verified archive.

---

## 9. What is still NOT a production deployment

- No managed PostgreSQL, no TLS terminator, no secrets manager and no private
  object storage are configured here; Compose binds to `127.0.0.1` and is
  explicitly labelled local/staging only.
- No `docker compose up` was executed in the development sandbox (no Docker
  binary). The rehearsal workflow on a GitHub runner is the evidence; until it
  is green, this runbook is a procedure, not a completed deployment.
- The staging acceptance gates A/B/C have not been re-run on `main` since the
  merges; `workflow_dispatch` from this session was denied (HTTP 403, token
  scope). Push-triggered or manually dispatched runs by an operator are needed.
