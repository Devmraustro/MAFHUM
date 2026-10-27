# Full-stack deployment — the honest gap and the concrete path

Date: 2026-10-27 · Status: **configuration is ready; no deployment has been
performed.** MAFHUM has no server, domain, managed database or credential of its
own. This document exists so an operator can close the gap without guessing.

## What exists today

| Layer | Reality |
|---|---|
| Web | https://mafhum-web.vercel.app/ — built and served on Vercel (Hobby). `GET /` returns the Arabic RTL page. |
| API | **Not hosted publicly.** It has only run locally and on ephemeral GitHub Actions runners. |
| Database | PostgreSQL 16 in Compose / pgserver in CI. No managed instance exists. |
| Blobs | Local filesystem `STORAGE_ROOT`. No object store and no encryption at rest. |
| TLS | None for the API. Vercel provides TLS for the web origin only. |
| Rewrites | `MAFHUM_API_URL=https://mafhum-api.invalid` at Vercel build time — intentionally non-resolvable. Every `/api` call returns 502. |

**This is why the Vercel deployment is described as a frontend-only rehearsal.**
The user explicitly chose on 2026-10-27 to **keep that deployment as is**; it is
not quietly upgraded or replaced here. No request, cookie or CSRF token can reach
a third party because `.invalid` never resolves.

## The configuration added in this pass

`deploy/` contains a self-hosted, zero-licence-cost stack:

- `docker-compose.prod.yml` — db + api + web + Caddy. Only Caddy publishes
  ports 80/443; the database is unreachable from outside the internal network.
  Named volumes keep PostgreSQL data and blobs across restarts.
- `Caddyfile` — automatic TLS via ACME, security headers, `/health/*` proxied
  directly to the API, everything else to Next.
- `.env.prod.example` — every required variable, with no real secret.

It was **not executed**: the sandbox has no Docker, no domain and no credentials.
What was verified is the equivalent three-service stack on a GitHub runner
(`deployment-rehearsal.yml` run `40530008866`, success). The prod file differs
only by TLS termination, `APP_ENV=production` and volume/secret wiring — those
differences are exactly what an operator must test on their host.

## Operator procedure (not performed here)

1. Provision a host with Docker Compose v2 and point a DNS A/AAAA record at it.
   **Confirm free-tier limits from the provider's current documentation before
   relying on them**; MAFHUM makes no promise of free 24/7 hosting.
2. Copy `deploy/.env.prod.example` to a secrets manager, fill
   `MAFHUM_DOMAIN`, `MAFHUM_ACME_EMAIL`, `POSTGRES_*`, `SESSION_SECRET` and
   `ALLOWED_ORIGINS=["https://<domain>"]`. Never commit the filled file.
3. From `deploy/`:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file <secrets> up -d --build --wait
   curl -fsS https://<domain>/health/ready
   curl -fsS -o /dev/null -w '%{http_code}\n' https://<domain>/
   ```
4. Register an account, upload a real Arabic PDF, confirm processing and
   download. Then take a backup **before** going live:
   ```bash
   docker compose -f docker-compose.prod.yml stop api
   python apps/api/tools/backup.py --database-url "$MAFHUM_DATABASE_URL" \
     --storage-root /var/lib/mafhum/storage --out ./backups \
     --require-quiesced --verify
   ```
5. Point the Vercel project's build-time `MAFHUM_API_URL` at
   `https://<domain>` and redeploy — **or** simply use the Caddy origin as the
   single public entry point and leave Vercel as the rehearsal it is. Both are
   valid; the user has chosen to leave Vercel unchanged for now.
6. Set the repository variable `MAFHUM_WEB_URL` to the chosen origin so
   `uptime-monitor.yml` watches the right place (see `docs/MONITORING.md`).
7. Re-run the rollback drill from `docs/ROLLBACK_GUIDE.md` against this host.

## Why we did not use a managed free database

MAFHUM's storage layer is local-filesystem only (`storage_backend: local`). A
serverless or ephemeral host would lose uploads on restart, and an object-store
adapter does not exist yet. A host with a persistent volume is the only
correct deployment target today. Adding S3-compatible storage is a separate
feature, not a deployment step.

## Remaining decisions (owner)

- Choose a licence. Decision recorded 2026-10-27: **no `LICENSE` file for now**.
  The repository remains all-rights-reserved by default; this must be revisited
  before inviting external contributors.
- Provide a domain and ACME contact.
- Decide whether to keep the Vercel frontend-only deployment (current choice:
  **keep**) after the API becomes public.
