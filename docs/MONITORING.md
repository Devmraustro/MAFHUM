# Monitoring

Date: 2026-10-27 · Status: **a free, real uptime probe is configured; a full
monitoring stack is not.**

## What actually runs

`.github/workflows/uptime-monitor.yml` contains two jobs:

| Job | Trigger | What it proves |
|---|---|---|
| `selftest` | every push/PR touching the probe or workflow | the probe returns 0 on a healthy Arabic page and **non-zero** on a page missing the product marker — so a broken monitor cannot silently report green |
| `live-probe` | `*/15 * * * *` schedule + `workflow_dispatch` (not on PRs) | `GET <MAFHUM_WEB_URL>` returns 200, contains `مَفْهُوم`/`MAFHUM`, and answers within 10 s |

The probe is `tools/monitoring/probe.py`: Python standard library only, no
credentials, no cookies, no tokens. It prints a JSON record (`url`, `status`,
`elapsed_ms`, `marker_found`, `error`) and emits a `::error::` annotation on
failure. The target defaults to the deliberately frontend-only Vercel
rehearsal URL; override it with the repository variable `MAFHUM_WEB_URL` once a
full-stack origin exists.

**Alert channel:** a failed scheduled GitHub Actions run notifies the repository
owner/watchers. There is no PagerDuty, Slack, email gateway or SMS integration.
That is a real but minimal alerting path — it depends on GitHub's notification
settings, which MAFHUM does not control.

## Admin metrics (scrape source, not yet scraped)

`GET /api/v1/admin/metrics` emits redacted Prometheus-style counters:
`mafhum_active_users`, `mafhum_documents_total{status=...}`,
`mafhum_presentation_jobs_total{status=...}`, `mafhum_audit_denied_total`.
Viewing it writes an `admin_metrics_viewed` audit row.

**Honest limitation:** that endpoint requires an authenticated admin session
cookie. A Prometheus server cannot scrape it without a provisioned credential,
and MAFHUM deliberately has no API-token or service-account mechanism yet. There
is no `prometheus.yml`, no alert rule set and no dashboard in this repository.
Adding a scoped, rate-limited metrics token (or scraping from inside the private
network with mTLS) is an operator task that remains open.

## Denial audit is bounded

Repeated non-admin probes of admin URLs previously wrote one audit row per
request. `AuditService.record_denied_once()` now writes at most one
`admin_access_denied` row per actor per 60 seconds, using a COUNT on an indexed
column in the database (so it holds across replicas). Two simultaneous first
requests may produce two rows; that is an acceptable bound. See
`apps/api/tests/test_admin_audit.py::TestHardeningFollowUps`.

## What is NOT monitored

- API `/health/ready` on a public origin — no public API origin exists yet.
- Database replication, disk fullness, backup age, certificate expiry (Caddy
  sends its own ACME notices, but no MAFHUM alert consumes them).
- Request latency/error-rate percentiles, user-facing synthetic transactions.
- Audit-table growth and retention (no retention policy is implemented).

## Operator checklist to finish this

1. Set repo variable `MAFHUM_WEB_URL` to the full-stack HTTPS origin.
2. Extend the probe with `--api-health-url` once the API is public.
3. Provision a scoped metrics credential (or an internal scrape endpoint) and
   add `prometheus.yml` + alert rules for `mafhum_audit_denied_total` spikes and
   `/health/ready` failures.
4. Decide the retention period for `audit_events` and archives; neither expires
   automatically today (see `docs/DATA_POLICY.md`).
5. Confirm GitHub notification delivery for failed scheduled runs.
