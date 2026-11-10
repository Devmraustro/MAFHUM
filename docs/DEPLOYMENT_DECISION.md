# Deployment decision record — 2026-11-10

The weakness this answers: *two deployment paths exist and neither has been
proven end to end with real credentials.* Rather than adding a third, this records
which one is canonical, what is proven about each, and what "proven" is allowed to
mean.

## Decision

**Canonical path: the container stack** — `docker-compose.yml` at the repository
root (PostgreSQL + API + web), deployed to any host that runs Docker. It is the
only path that runs the document worker as a resident process, which is what the
product's upload → parse → search loop needs.

**The Vercel/serverless path is explicitly secondary and incomplete.** It exists,
is configuration-validated, and its gaps are documented rather than papered over.
It is *not* the recommended way to run MAFHUM today.

## What is actually proven

| Claim | Evidence | Verified when |
|---|---|---|
| API image builds, runs non-root, becomes ready, shuts down gracefully | GitHub Actions run `40377229156` (Gate B) | earlier |
| `docker compose up` of the full stack works | deployment-rehearsal workflow runs `40530008866`, `40593579965` | earlier |
| Compose file and env contract are internally consistent | `apps/api/tests/test_config_contract.py` (6 tests) and the compose validation test | this session |
| Serverless API answers under a function runtime | **Not verified.** No deployment was performed. | — |
| Vercel Blob storage under real credentials | **Not verified.** No Blob store exists. | — |
| External `clamd` reachable from a function | **Blocked.** Functions cannot reach a local socket; §6 of the serverless runbook records it. | — |
| Migrations applied to a real managed database | Only against a local cluster (`alembic upgrade head` → `0011`) | this session |

## What is deliberately not done

- **No Vercel project was created and nothing was deployed.** The account is
  connected but unused: a deployment cannot be honestly verified without a
  database, a Blob store and a scanner host, and creating those is a billing and
  ownership decision that belongs to the repository owner.
- No secrets were generated or committed; `.env.example` lists names only.

## If you want to deploy tomorrow

1. Follow `docs/DEPLOYMENT_RUNBOOK.md` for the container path.
2. Set `PUBLIC_WEB_URL` to the real origin and configure `EMAIL_DELIVERY=smtp` —
   without it, password recovery honestly answers `503` (see
   `docs/ACCOUNT_LIFECYCLE.md`).
3. Run `alembic upgrade head`, then create the first admin by CLI.
4. Send yourself one password reset before telling anyone recovery works.

Choose the serverless path only if you accept: no resident worker (processing
depends on a tick), chunked uploads for anything over 4 MiB, and no malware
scanning until an external scanner host exists.
