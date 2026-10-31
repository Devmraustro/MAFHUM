# Monitoring

Date: 2026-10-28 · Status: **a free uptime probe runs; a scoped metrics
endpoint, a scrape configuration and real alert rules now exist, but no
monitoring stack is deployed and no alert is delivered to a human.** The public
API does not exist yet, so these are production *prerequisites*, not live
monitoring.

## 1. What actually runs today

`.github/workflows/uptime-monitor.yml` contains two jobs:

| Job | Trigger | What it proves |
|---|---|---|
| `selftest` | every push/PR touching the probe or workflow | the probe returns 0 on a healthy Arabic page and **non-zero** on a page missing the product marker — so a broken monitor cannot silently report green |
| `live-probe` | `*/15 * * * *` schedule + `workflow_dispatch` (not on PRs) | `GET <MAFHUM_WEB_URL>` returns 200, contains `مَفْهُوم`/`MAFHUM`, and answers within 10 s |

The probe is `tools/monitoring/probe.py`: Python standard library only, no
credentials, no cookies, no tokens. The target defaults to the deliberately
frontend-only Vercel rehearsal URL; override it with the repository variable
`MAFHUM_WEB_URL` once a full-stack origin exists.

**Alert channel today:** a failed scheduled GitHub Actions run notifies the
repository owner/watchers. There is no PagerDuty, Slack, email gateway or SMS
integration. That is a real but minimal path and depends on GitHub notification
settings MAFHUM does not control.

## 2. Scoped metrics authentication

`GET /api/v1/admin/metrics` accepts two disjoint principals:

| Principal | How | Result |
|---|---|---|
| Admin session | normal session cookie + database-backed `admin` role | metrics returned; `admin_metrics_viewed` audit row written |
| Scoped bearer token | `Authorization: Bearer $METRICS_SCRAPE_TOKEN` | metrics returned; **no** audit row |

The token is checked in `apps/api/src/mafhum_api/api/deps.py::metrics_access`
with a constant-time comparison. It is accepted on **this one route only**; it
cannot read `/admin/overview`, `/admin/usage`, `/admin/audit-events` or any user
data. A request that supplies a Bearer header is never allowed to fall back to a
cookie session, so a wrong token fails with 401 even if the caller has an admin
cookie. An empty `METRICS_SCRAPE_TOKEN` disables the bearer path entirely.

`Settings.assert_production_ready()` refuses to boot in production when the
token is missing or shorter than 32 characters. `deploy/.env.prod.example`
carries only a `REPLACE_WITH_...` placeholder. Generate a separate secret (never
reuse `SESSION_SECRET` or the database password):

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Token rotation: change the value in the host secret store, restart the API,
then update the file mounted into Prometheus. Both old and new tokens are not
accepted simultaneously — rotate the scraper first is **not** required because
the API rejects the new token until it is restarted; plan a short scrape gap.

Token scrapes are deliberately not audited: a 30-second scrape would otherwise
add 2,880 rows per day to the append-only log and drown the denial signal.
Human admin views are still audited.

## 3. Metrics emitted

From `apps/api/src/mafhum_api/api/v1/admin.py` (aggregate counters only — no
email, filename, URL, token or document text):

| Metric | Type | Meaning |
|---|---|---|
| `mafhum_up` | gauge | always 1 when the endpoint answered |
| `mafhum_active_users` | gauge | accounts not pending deletion |
| `mafhum_documents_total{status=...}` | gauge | documents by processing status |
| `mafhum_processing_pending` | gauge | pending + processing documents |
| `mafhum_presentation_jobs_total{status=...}` | gauge | generated decks |
| `mafhum_presentation_artifacts_total` | gauge | stored artifacts |
| `mafhum_audit_events_total` | gauge | audit rows stored |
| `mafhum_audit_denied_total` | counter | denied admin/audit rows stored |
| `mafhum_malware_scan_required` | gauge | 1 when fail-closed scanning is on |
| `mafhum_malware_scanner_available` | gauge | 1 when clamd answers PING **right now** |

`tools/acceptance/prod_compose_contract.py` parses the alert rules and asserts
that every `mafhum_*` series they reference is actually emitted, so a rule can
never silently depend on a metric that does not exist.

## 4. Scrape configuration and alert rules (templates, not deployed)

| File | Purpose |
|---|---|
| `deploy/prometheus/prometheus.yml` | scrapes `http://api:8000/api/v1/admin/metrics` on the **internal** Docker network, with `bearer_token_file`; 30 s interval, 10 s timeout |
| `deploy/prometheus/alert.rules.yml` | 7 rules: API down, missing target, ClamAV unavailable while scanning required, denied-event spike, documents stuck processing, failed documents, audit-table growth |
| `deploy/prometheus/alertmanager.yml` | groups and inhibits alerts; **has no delivery destination** |
| `deploy/docker-compose.monitoring.yml` | optional Prometheus + Alertmanager stack, started separately |

Scraping over the internal network means metrics never cross the public
internet. Caddy also routes `/api/v1/admin/metrics` directly to the API (see
`deploy/Caddyfile`) for operators who prefer an HTTPS scrape; that path is still
token-protected.

Image tags in the monitoring Compose file are defaults, not verified digests.
Prometheus and Alertmanager are Apache-2.0. Grafana is AGPL-3.0 and is
intentionally not bundled; adding a dashboard would impose its licence
obligations and needs an owner decision.

### Offline validation gate (runs in CI on every commit)

`python tools/monitoring/validate_monitoring.py` validates the committed
configuration without needing Prometheus binaries:

* YAML structure of all three files, plus internal references (rule files
  exist, every route/inhibit receiver exists, every matcher parses).
* **PromQL syntax of every alert rule**, checked with the `promql-parser`
  grammar (MIT, Rust-based). This is the repository-side equivalent of
  `promtool check rules`; promtool on the monitoring host remains the
  authoritative check.
* **Rule ↔ exporter contract**: every metric a rule references is emitted by
  `apps/api/src/mafhum_api/api/v1/admin.py` (or is Prometheus' own `up`).
* **Rule behaviour scenarios**: every rule is evaluated over synthetic series
  by a small AST-driven evaluator, asserting both the documented firing case
  and the documented non-firing case (including `for:` persistence, strict `>`
  thresholds, and label-selective matching).
* With `--live URL --token TOKEN`, it scrapes a running exporter and verifies
  every referenced metric is actually present in the live exposition.

The gate fails loudly (`RESULT: MONITORING VALIDATION FAILED`) on any
regression. It does **not** prove delivery — see the operator steps below.

### Operator steps (not done — requires the owner)

1. Create `deploy/secrets/` on the host (it is gitignored) and write the token
   to `deploy/secrets/metrics_token` with mode `0600`. Never commit it.
2. Verify the Prometheus/Alertmanager image tags on the target host and pin them
   by digest.
3. Edit `deploy/prometheus/alertmanager.yml` to add real `email_configs` or
   `webhook_configs` with owner-supplied credentials. Until then alerts are
   visible in the Alertmanager UI/API but are **not delivered anywhere**.
4. Start `deploy/docker-compose.monitoring.yml` after the production stack.
5. Decide the Prometheus retention period (`--storage.tsdb.retention.time`);
   30 d is the template default, not an approved policy.
6. Confirm the alert actually arrives at the chosen destination by firing
   `MafhumApiDown` in a rehearsal. A green `amtool check-config` is not proof of
   delivery.

## 5. Denial audit is bounded

Repeated non-admin probes of admin URLs previously wrote one audit row per
request. `AuditService.record_denied_once()` writes at most one
`admin_access_denied` row per actor per 60 seconds, using a COUNT on an indexed
column in the database (so it holds across replicas). Two simultaneous first
requests may still produce two rows; that is an acceptable bound. See
`apps/api/tests/test_admin_audit.py::TestHardeningFollowUps`.

Audit retention is configurable but **disabled by default** (0 = keep forever).
`python apps/api/tools/purge_audit.py --days N` is a dry run; `--apply` is
required to delete. See `docs/DATA_POLICY.md`.

## 6. What is still NOT monitored

- API `/health/ready` on a public origin — no public API origin exists yet.
- Host disk fullness, inode exhaustion, PostgreSQL replication, backup age,
  certificate expiry and ClamAV signature *freshness* (the scanner availability
  gauge only proves the daemon answers PING, not that signatures are current).
- Request latency/error-rate percentiles, user-facing synthetic transactions.
- Backup/restore success on a schedule, and log retention.
- Any dashboard. No time-series visualisation is deployed.

## 7. Remaining operator checklist

1. Set repo variable `MAFHUM_WEB_URL` to the full-stack HTTPS origin.
2. Extend the probe with `--api-health-url` once the API is public.
3. Supply `METRICS_SCRAPE_TOKEN` and start the monitoring stack.
4. Supply an alert destination and prove delivery.
5. Decide retention for `audit_events`, Prometheus data and backups.
6. Confirm GitHub notification delivery for failed scheduled runs.
