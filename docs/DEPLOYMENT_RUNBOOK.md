# MAFHUM deployment runbook

Date: 2026-10-28 · Applies to the 2026-10-28 review branch (based on `main`
`2a15f0c`) and later.

This is an operator procedure, not a claim that production is running. Every
command below is either (a) already exercised in CI, or (b) explicitly marked
**operator action**. Nothing here requires a paid service.

**Prerequisite change:** production now REQUIRES a real ClamAV engine, a
mandatory `MALWARE_SCAN_REQUIRED=true`, and a scoped `METRICS_SCRAPE_TOKEN` of at
least 32 characters. `assert_production_ready()` refuses to start without them.
Do not start from an older copy of `deploy/.env.prod.example`.

---

## 1. What gets deployed

| Component | Artefact | Verified where |
|---|---|---|
| PostgreSQL 16 | `postgres:16` (Compose) | Migrations job + deployment rehearsal |
| FastAPI API | `apps/api/Dockerfile` | Gate B (build/non-root/readiness/shutdown) + deployment rehearsal |
| Next.js web | `apps/web/Dockerfile` (Phase 7) | Local runtime-layout check + deployment rehearsal |
| ClamAV daemon | `deploy/clamd/Dockerfile` | `production-malware-acceptance.yml` (real engine + EICAR + outage) |
| Caddy TLS | `deploy/Caddyfile` | `docker compose config` only — **not deployed** |
| Prometheus / Alertmanager (optional) | `deploy/docker-compose.monitoring.yml` | Config templates only — **not deployed** |
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
MALWARE_SCAN_REQUIRED=true                  # MANDATORY in production
CLAMD_HOST=clamd                            # or CLAMD_UNIX_PATH; one is required
CLAMD_PORT=3310
METRICS_SCRAPE_TOKEN=<separate random 48+ bytes, >= 32 chars>  # MANDATORY
AUDIT_RETENTION_DAYS=0                     # 0 = keep forever until policy decided
```

`deploy/docker-compose.prod.yml` hard-codes the malware flag and the daemon
address, and requires `METRICS_SCRAPE_TOKEN` as a `${VAR:?...}` interpolation —
Compose itself refuses to start if it is unset. The token must be **distinct**
from `SESSION_SECRET` and the database password; it grants access to
`/api/v1/admin/metrics` only.

`RESEARCH_RETRIEVAL_ENABLED=false` by default; enabling it permits outbound
fetches that must be reviewed against `docs/SSRF_REVIEW.md`. OCR, an AI provider
and live-source provenance are **not implemented** in this release.

---

## 3. Pre-deploy checklist

0. **Validate the production Compose contract before touching the host.** These
   commands run in CI on every PR and must also be run once with the operator's
   real (uncommitted) env file:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml \
     --env-file deploy/.env.prod.example config --quiet
   python tools/acceptance/prod_compose_contract.py
   python tools/security_baseline.py
   ```
   The contract asserts a real `clamd` service with a SCAN-based healthcheck,
   `api` gated on `clamd: service_healthy`, a hard-coded
   `MALWARE_SCAN_REQUIRED=true`, required secret interpolations, pinned image
   tags, no published ports except Caddy, and alert rules that reference only
   metrics the API actually emits. **A parseable Compose file is not a passing
   contract.**

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
4. Confirm `SESSION_SECRET`, `COOKIE_SECURE`, `METRICS_SCRAPE_TOKEN`,
   `MALWARE_SCAN_REQUIRED=true` and `CLAMD_HOST` are set; the entrypoint refuses
   to start production otherwise.
5. Confirm the ClamAV volume has a fresh signature set and that the daemon's
   healthcheck passes a real `clamdscan`. Verify an EICAR probe is rejected by
   the engine **before** allowing user uploads (see
   `.github/workflows/production-malware-acceptance.yml` for the exact sequence).
   The entire Phase-2 gate — build, cold bootstrap timing to SCAN-READY,
   three-case acceptance, outage fail-closed, recovery with volume reuse, the
   12 h refresh loop and (with `--full --confirm-isolated-test`) a repeat cold
   bootstrap — is automated as one command for any Docker host:
   `python tools/clamav/production_bootstrap_acceptance.py --env-file <env>`
   (exit 0 = every phase passed; a timed report is written to
   `.clamav-acceptance/<isolated-project>/report.md`).

   **Safety model (v2, remediated 2026-10-31).** The tool never touches the
   production stack. Each run creates its own throwaway Compose project
   (`mafhum-clamav-acc-<timestamp>-<nonce>`); the names `mafhum-prod` and
   `mafhum` are reserved and rejected, including by prefix. Every compose
   command carries an explicit `--project-name` for the isolated project;
   images are built under run-unique tags (the production tags are never
   rebuilt); the api is published on `127.0.0.1` only. A preflight aborts on
   any ambiguity (project-name collision, containers or volumes already
   carrying the run's label, resource-name collisions). By default **no
   volume is ever deleted** — the isolated project's first `up` *is* the cold
   bootstrap, and cleanup is `docker compose down` without `-v` (evidence is
   preserved). The only volume deletion is the optional second cold bootstrap
   (`--full`), which additionally requires `--confirm-isolated-test` and a
   triple gate: the volume was created by this run, its
   `com.docker.compose.project` label matches the isolated project, and its
   exact name is re-verified immediately before removal. After cleanup the
   tool audits every docker command it issued and fails the run if any could
   have addressed a production resource. This is enforced by
   `apps/api/tests/test_clamav_acceptance_safety.py` (mocked Docker CLI, no
   real host needed) on every commit.

   **Three levels of malware-gate evidence — do not conflate them:**
   - *CI engine tests* (`.github/workflows/production-malware-acceptance.yml`,
     ubuntu runner with its own Docker): prove the compose contract and the
     real fail-closed behaviour on a fresh, throwaway stack. They prove
     nothing about your production host.
   - *Isolated local acceptance* (this tool, any Docker host, default flags):
     proves the same phases on hardware you control, still without touching
     any existing deployment.
   - *Production-host acceptance*: the only evidence that the real
     `mafhum-prod` stack behaves as designed. It is an owner action — run the
     tool on the production host (it stays isolated by construction), then
     confirm the live engine with `docker exec mafhum-prod-clamd-1 clamdscan
     --version` and an EICAR probe through the public API before enabling
     uploads.
6. Decide the audit-retention policy with the owner. Do **not** schedule
   `purge_audit.py --apply` until a legal hold has been ruled out
   (`docs/DATA_POLICY.md` §8).

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

- `/health/ready` reports `database=ok`, `storage=ok`, `processing_worker=ok`
  **and `malware_scanner=ok`**. Anything else means the stack is not ready to
  accept uploads.
- `python tools/e2e/compose_smoke.py` prints `ALL COMPOSE SMOKE CHECKS PASSED`.
- `docker compose ps` shows every service healthy, including `clamd`.
- An admin can read `GET /api/v1/admin/overview` and
  `GET /api/v1/admin/metrics`; a normal user gets `404 resource_not_found`.
- `GET /api/v1/documents/limits` reports
  `malware_scanning_available: true` — if it is false, stop and fix the engine.
- With the monitoring stack started, a request carrying
  `Authorization: Bearer $METRICS_SCRAPE_TOKEN` returns metrics, the same token
  is rejected on `/admin/overview`, and alerts are visible in Alertmanager.
  **Delivery to a human is not configured by default** — see §6.

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

**Honest limit:** a free scheduled uptime probe exists, and a scoped metrics
token plus `deploy/prometheus/prometheus.yml`, `alert.rules.yml` and an optional
`deploy/docker-compose.monitoring.yml` are now available. **They are templates,
not a running monitoring system.** `deploy/prometheus/alertmanager.yml` has an
`unconfigured` receiver with **no delivery destination**: alerts are grouped and
visible in the UI but reach nobody until the owner adds real `email_configs` or
`webhook_configs` with credentials. There is still no dashboard, no log
aggregation, no container-image scanning and no penetration test. See
`docs/MONITORING.md` and `docs/PENTEST_READINESS.md`.

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
described as such. The executed rehearsal (project `mafhum-web`, deployment
`dpl_Hhpr51CPn24fZT91yisV78S4i6oy`, URL https://mafhum-web.vercel.app/) is
recorded in `docs/DEPLOYMENT_REHEARSAL_REPORT.md`: `GET /` returned the Arabic
RTL page, while `GET /health/live` returned 502 because the API origin is a
deliberately non-resolvable placeholder.

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
  binary). It **was** executed on a GitHub runner in workflow run `40530008866`
  (success), so the procedure is verified there — but that is a rehearsal on
  ephemeral CI infrastructure, not a running production service.
- Staging gates A/B/C **were** re-run automatically on `main` after PR #2
  merged (run `40523074856`) and again after PR #3 merged (run
  `40590637500`) — all three gates green each time. A manual
  `workflow_dispatch` from this session is still denied (HTTP 403, token scope),
  but the push trigger on `main` works and is the supported way to re-run them.
