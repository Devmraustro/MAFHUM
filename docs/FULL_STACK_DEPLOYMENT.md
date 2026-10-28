# Full-stack deployment — the honest gap and the concrete path

Date: 2026-10-28 (updated) · Status: **configuration is ready and statically
validated; no deployment has been performed.** MAFHUM has no server, domain,
managed database or credential of its own. The production blueprint now includes
a real ClamAV service, mandatory fail-closed scanning and a scoped metrics token;
`docker compose config` and `tools/acceptance/prod_compose_contract.py` validate
that wiring on CI, but **no host has run the production stack**. This document
exists so an operator can close the gap without guessing.

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

- `docker-compose.prod.yml` — **clamd** + db + api + web + Caddy. Only Caddy
  publishes ports 80/443; the database, API, web and ClamAV stay on the internal
  network. Named volumes keep PostgreSQL data, blobs and ClamAV signatures across
  restarts. `api` waits for `clamd: condition: service_healthy`.
- `clamd/` — a small Debian-based image with an explicit TCP-only config, a
  `freshclam` bootstrap, a 12-hourly refresh loop and a healthcheck that performs
  a real `clamdscan` (a TCP connect is not proof of a working engine).
- `Caddyfile` — automatic TLS via ACME, security headers, `/health/*` and
  `/api/v1/admin/metrics` proxied directly to the API, everything else to Next.
- `.env.prod.example` — every required variable, with placeholders only.
- `prometheus/` and `docker-compose.monitoring.yml` — optional scraping and alert
  rules. **No alert destination is configured**; see `docs/MONITORING.md`.
- `tools/acceptance/prod_compose_contract.py` (repo root) — a static contract
  asserting fail-closed wiring, required secret interpolations, pinned image tags
  and alert rules that reference only real metrics.

**Validation performed without a host:** `docker compose -f
deploy/docker-compose.prod.yml --env-file deploy/.env.prod.example config
--quiet` and the contract checker both run in CI
(`.github/workflows/production-malware-acceptance.yml`). A separate workflow
builds `deploy/clamd/`, waits for its healthcheck and verifies EICAR detection,
clean acceptance and engine-outage rejection through the real API image.
The **dev** three-service stack was fully exercised by `deployment-rehearsal.yml`
(runs `40530008866`, `40593579965`). The **production** stack has never been
started: TLS/ACME, real host volumes, signature refresh and backup/restore on
that host remain operator actions.

## Operator procedure (not performed here)

1. Provision a host with Docker Compose v2 and point a DNS A/AAAA record at it.
   **Confirm free-tier limits from the provider's current documentation before
   relying on them**; MAFHUM makes no promise of free 24/7 hosting.
2. Copy `deploy/.env.prod.example` to a secrets manager and fill **every**
   value: `MAFHUM_DOMAIN`, `MAFHUM_ACME_EMAIL`, `POSTGRES_USER`,
   `POSTGRES_PASSWORD`, `POSTGRES_DB`, `SESSION_SECRET`,
   `ALLOWED_ORIGINS=["https://<domain>"]`, and a **separate** high-entropy
   `METRICS_SCRAPE_TOKEN` (>= 32 chars). Leave `MALWARE_SCAN_REQUIRED=true` and
   `CLAMD_HOST=clamd`. Set `AUDIT_RETENTION_DAYS` only after approving a
   retention policy. Never commit the filled file; `secrets/` is gitignored.
3. Validate before starting anything:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --env-file <secrets> config --quiet
   python tools/acceptance/prod_compose_contract.py
   python tools/security_baseline.py
   ```
   All three must exit 0. A parseable file is not a passing contract.
4. From the repository root:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --env-file <secrets> \
     up -d --build --wait          # waits for clamd's SCAN healthcheck
   curl -fsS https://<domain>/health/ready
   curl -fsS -o /dev/null -w '%{http_code}\n' https://<domain>/
   ```
   `/health/ready` must report `malware_scanner: ok`. If it says
   `unavailable`, **do not accept uploads**.
5. Verify the malware control on THIS host with the EICAR probe (the published,
   non-malicious test string): a clean file must be accepted, the EICAR DOCX
   produced by `apps/api/tools/eicar_probe.py` must be rejected with
   `details.reason = malware_detected`, and stopping `clamd` must cause
   rejection with a `scan_*` reason — never `CLEAN` and never a false
   `malware_detected`. The exact sequence is in
   `.github/workflows/production-malware-acceptance.yml`.
6. Register an account, upload a real Arabic PDF, confirm processing and
   download. Then take a backup **before** going live:
   ```bash
   docker compose -f docker-compose.prod.yml stop api
   python apps/api/tools/backup.py --database-url "$MAFHUM_DATABASE_URL" \
     --storage-root /var/lib/mafhum/storage --out ./backups \
     --require-quiesced --verify
   ```
7. **Optional monitoring:** create `deploy/secrets/metrics_token` (mode `0600`,
   gitignored) containing the same `METRICS_SCRAPE_TOKEN`, verify the
   Prometheus/Alertmanager image tags and pin them by digest, add a real alert
   destination to `deploy/prometheus/alertmanager.yml`, then
   `docker compose -f deploy/docker-compose.monitoring.yml up -d`. Fire a test
   alert and **prove delivery** — a green config is not proof.
8. Point the Vercel project's build-time `MAFHUM_API_URL` at
   `https://<domain>` and redeploy — **or** simply use the Caddy origin as the
   single public entry point and leave Vercel as the rehearsal it is. Both are
   valid; the user has chosen to leave Vercel unchanged for now.
9. Set the repository variable `MAFHUM_WEB_URL` to the chosen origin so
   `uptime-monitor.yml` watches the right place (see `docs/MONITORING.md`).
10. Re-run the rollback drill from `docs/ROLLBACK_GUIDE.md` against this host.
11. Work through `docs/PENTEST_READINESS.md` before declaring the service ready.

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
- Provide a host, a domain, DNS control and an ACME contact.
- Supply real values for every secret in `deploy/.env.prod.example`, including a
  separate `METRICS_SCRAPE_TOKEN`; store them in a secrets manager.
- Choose a KMS/HSM and complete `docs/ADR/0006-encryption-at-rest.md` before
  enabling any field encryption; decide volume/disk encryption on the host.
- Approve an audit-retention and backup-retention policy (and rule out legal
  hold) before scheduling `purge_audit.py --apply`.
- Supply an alert destination and prove a test alert is delivered.
- Decide whether to keep the Vercel frontend-only deployment (current choice:
  **keep**) after the API becomes public.
- Authorise (separately) a penetration test, a production deployment, any DNS
  change, any repository-visibility change, and any PR merge.

## What is explicitly NOT claimed

- No production deployment, public API, TLS certificate or DNS record exists.
- No field encryption is enabled and no KMS is configured — ADR 0006 is a design
  plus synthetic-key tests only.
- No alert is delivered to a human; no dashboard, log aggregation or DAST exists.
- The headless LibreOffice PPTX render in CI is not PowerPoint visual parity.
- OCR, an AI provider and live-source content provenance are out of scope.
