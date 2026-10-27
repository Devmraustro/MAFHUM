# Deployment rehearsal report

Date: 2026-10-27 · Branch: `arena/71aa7382-mafhum` (PR #3) · Head: `0f77e2b3`

This records **executed** deployment work. It does **not** claim that MAFHUM is
in production. Two different kinds of rehearsal were performed, and they are
reported separately because they prove different things.

---

## 1. Full stack on a Docker runner — PASSED

Workflow: `.github/workflows/deployment-rehearsal.yml`
Run: **`40530008866`** (pull_request, head `0f77e2b3`) — conclusion **success**,
all 10 steps green in 1m48s on `ubuntu-24.04`.

| Step | Result |
|---|---|
| Install PostgreSQL 16 client + Python verification deps | success |
| `docker compose up -d --build --wait` (db + api + web) | success |
| Health: API `/health/live`, `/health/ready`, web `/`, web→API rewrite | success |
| `python tools/e2e/compose_smoke.py` (register → upload → process → download → admin gate) | success |
| Quiesced backup → restore into `mafhum_restore_check` → checksum verification | success |
| Teardown | success |

What this closes: **`docker compose up` was previously unverified** (no container
runtime in the development sandbox). It has now been executed on real
infrastructure, with all three images built from this repository, a real
PostgreSQL 16, real blob storage, a real HTTP round-trip and a real restore
drill. The restore drill proves the archive is usable: every `storage_key` in the
restored database resolves to a blob whose SHA-256 matches the manifest.

Three failures were found and fixed while getting there, all recorded in git:

1. `ubuntu-latest` had moved to Ubuntu 26 (PostgreSQL 18 client), so
   `postgresql-client-16` was unavailable → the runner is pinned to
   `ubuntu-24.04`.
2. `next.config.mjs` evaluates `rewrites()` at **build** time; the Compose
   `MAFHUM_API_URL` runtime variable was therefore ignored and the web container
   proxied to its own `127.0.0.1:8000` → the value is now a Dockerfile `ARG`
   passed by Compose as `build.args`, and `apps/web/vercel.json` fails the build
   when the variable is missing.
3. The smoke journey used a `.invalid` email, which `email-validator` rejects →
   replaced with the reserved `example.org` domain, and every check now emits a
   CI annotation on failure.

### Related evidence

- Staging acceptance gates A/B/C re-ran automatically on `main` at `a4b90d11`
  after PR #2 merged: run **`40523074856`**, conclusion **success** (Gate A real
  ClamAV/EICAR, Gate B container build/runtime, Gate C external TLS).
- `MAFHUM CI` on PR #3 head `0f77e2b3`: run **`40530009023`** (and the earlier
  `40527952019`, `40528767578`) — 4/4 jobs green.
- Local runtime-layout check of the web image (prod-only `node_modules` + `.next`
  + `next.config.mjs`): `next start` returned HTTP 200 with `<html lang="ar" dir="rtl">`.

---

## 2. Vercel web deployment — BUILT AND SERVED (frontend only)

| Field | Value |
|---|---|
| Project | `mafhum-web` (`prj_2agVJ0QhEZYzxj54EDWClzEi0mvA`) |
| Deployment | `dpl_Hhpr51CPn24fZT91yisV78S4i6oy` |
| State | **READY / PROMOTED**, `target=production`, Hobby plan |
| URL | https://mafhum-web.vercel.app/ |
| Source | `Devmraustro/MAFHUM` @ `arena/71aa7382-mafhum`, commit `0f77e2b3` |
| Root / framework / Node | `apps/web` / Next.js 16.4.2 / 24.x |
| Build time | 31.8 s (Turbopack) |
| Build env | `MAFHUM_API_URL=https://mafhum-api.invalid` (deliberately non-resolvable) |

**Verified over real HTTP:** `GET /` returns HTTP 200, title
"MAFHUM — منصة تعليمية وبحثية للطلبة", Arabic RTL login form. This proves the
web tier builds and serves on real infrastructure with the committed
`vercel.json`, `next.config.mjs` and dependency lockfile.

**Known and expected failure (measured):** `GET /health/live` through the Next
rewrite returns **502 `DNS_HOSTNAME_NOT_FOUND`** because the API origin is a
placeholder. No API is hosted on Vercel, so **every `/api` call fails**. This is
a **frontend-only rehearsal**, not a working full-stack product. The placeholder
uses the reserved `.invalid` TLD so no request, cookie or CSRF token can ever
reach a third party.

To make this a real deployment, an operator must host the FastAPI service on a
free-tier (or self-hosted) origin with managed PostgreSQL, set
`MAFHUM_API_URL` to that origin **at build time**, add it to the API's
`ALLOWED_ORIGINS`, and re-deploy. That is an explicit remaining task, not done
here.

---

## 3. What is still not a production deployment

- No public API origin, TLS terminator, managed database, secrets manager or
  private object storage.
- No external monitoring/alerting attached to `/api/v1/admin/metrics`.
- No domain name was added; only Vercel's automatic `.vercel.app` aliases exist.
- No human has opened a generated PPTX in PowerPoint/LibreOffice (no such
  application in the verification environment).
- No `LICENSE` file exists yet (the repository reports `licenseInfo: null`);
  selecting one is the owner's decision.

A green rehearsal proves the deployment *procedure* works. It does not prove
operational readiness, capacity, or security under hostile traffic.
