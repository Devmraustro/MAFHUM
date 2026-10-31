# Project completion phase — 2026-10-31 (Arena session)

Executor: Arena agent, branch `arena/37adf787-mafhum`, continuing from the
operational-verification phase (`docs/OPERATIONAL_VERIFICATION_REPORT.md`,
PR #8). This document records what was executed, what was fixed, what was
automated, what was merged, and — honestly — what still belongs to the owner.

**Headline:** the CI regression on PR #8's head was found and fixed (pypdf
advisories), the entire CI matrix was reproduced green locally on Linux, the
monitoring configuration gained an executed offline validation gate, the local
authenticated DAST was **executed for the first time** (86/86, one low finding
remediated), the production ClamAV gate became a one-command runbook, and the
whole PR stack was landed on `main` (PRs #6, #7, #8, #9 and #10, every merge
record preserved). What still cannot be done from here — real-engine container
evidence, live alert delivery to a real destination, PowerPoint visual parity,
the staging DAST, and enabling branch protection (the session token lacks
Administration write) — is precisely enumerated in §6–§7.

## 1. CI regression found and fixed (before any merge)

`Backend — lint, types, tests` on `12386c1` (PR #8 head) was **red**: the
`pip-audit` step failed on **pypdf 6.21.2** with seven GHSA advisories
(GHSA-62m5-6wh8-28x7, GHSA-42gh-23hr-vphw, GHSA-x3rh-gjgm-fmqc,
GHSA-4vqv-7265-gm2w fixed in 6.21.3; GHSA-5849-834g-hvw5, GHSA-mfr5-77xh-9j7h,
GHSA-cggf-277g-3q54 fixed in 6.21.4). Fixed by pinning **pypdf 6.21.4**
(commit `92afdd0`), with version references updated in `THIRD_PARTY_NOTICES.md`
and `docs/OSS_REUSE_REGISTER.md`.

## 2. Full CI matrix reproduced locally (Linux, Python 3.11.2) — all green

| CI job / gate | Local result |
|---|---|
| Ruff lint + format (backend) | pass |
| Mypy strict (72 files) | pass |
| pytest against real PostgreSQL (pgserver) | **974 passed, 6 skipped, 184 s** |
| pip-audit | no known vulnerabilities (after the bump) |
| Bandit SAST (`-ll`) | pass |
| `tools/security_baseline.py` | ALL PASSED |
| `tools/acceptance/prod_compose_contract.py` | ALL PASSED |
| Frontend typecheck / lint / tests / build | pass (107 tests) |
| apps/slides tests + npm audit | pass (2 tests, 0 vulnerabilities) |
| PPTX benchmark (both generators) | both decks editable, RTL, Arabic+French, no issues |
| PPTX visual pre-flight (`--rtl`) | both decks `ok=true`, no problems, no warnings |

## 3. Monitoring: from config audit to an executed, committed gate

`tools/monitoring/validate_monitoring.py` (new, committed, wired into the CI
security job): **81 checks, all passing** — YAML structure and cross-references;
**PromQL syntax of all 7 alert rules** via `promql-parser` (MIT); the
rule-metric ↔ exporter contract against `admin.py`; Alertmanager
routing/receiver/matcher/inhibit consistency; and **firing + non-firing
behaviour scenarios for every rule** evaluated over synthetic series by a small
AST-driven evaluator (`for:` persistence, strict `>` thresholds, label-selective
matching). `--live URL --token` verifies a running exporter's exposition.
`promql-parser==0.11.0` added to dev extras; the missing `bandit`/`PyYAML`
rows were added to `THIRD_PARTY_NOTICES.md`. promtool on the monitoring host
remains the authoritative check (no Prometheus binaries exist for this
sandbox's allowed registries — verified, not assumed).

## 4. DAST: prepared → **executed** (local, authenticated, production posture)

`tools/dast/local_dast.py` (new, committed, wired into CI as a required job)
boots the target itself — real PostgreSQL, real migrations, self-signed TLS so
`Secure` cookies are real — and runs **two uvicorn instances in production
posture** (engine stub reachable / engine unreachable) against the
`docs/PENTEST_READINESS.md` §4 scope with synthetic data only.

**Result at `17d6313`: 86 checks passed, 0 failed** across authentication,
session/CSRF, tenant isolation (anti-enumeration included), upload validation
(magic/MIME/size/zip-bomb/traversal-neutralization), the fail-closed malware
policy (EICAR via stub → `malware_detected`; engine outage → `scan_error`,
never CLEAN), admin surface, scoped metrics boundaries, export isolation,
account deletion (blobs removed, audit rows survive with NULL actor), error
hygiene, the login throttle (to the documented limit, then stop) and Caddy
header posture.

**Finding F-1 (low, remediated):** no `Content-Security-Policy` anywhere in
the transport template. Remediated in `deploy/Caddyfile` with a conservative
self-only CSP (`object-src 'none'`, `base-uri 'self'`, `frame-ancestors
'none'`), pinned by a DAST assertion; browser verification during the
deployment rehearsal and nonce-tightening are documented owner follow-ups.
Full report: `docs/DAST_LOCAL_REPORT_2026-10-31.md`.

Explicit no-claims: the clamd daemon is a **protocol stub** (no detection
claim — real-engine EICAR stays proven by
`production-malware-acceptance.yml`); the scan is scripted, not interactive
ZAP/Burp; TLS header posture was verified at the configuration level.

## 5. Production ClamAV gate: manual runbook → one command

`tools/clamav/production_bootstrap_acceptance.py` (new, committed) automates
the whole Phase-2 runbook on any Docker host: compose config validation,
timed build, cold bootstrap with a fresh signature volume (health polling,
SCAN-READY timing, freshclam evidence), db+api start via a generated gitignored
port override, the three-case acceptance through the real API (reusing the
exact `malware_fail_closed.py` helpers), engine-outage fail-closed, recovery
with volume-reuse proof, the 43 200 s refresh-loop liveness check, and
`--full --confirm-isolated-test` for a repeat cold bootstrap. Cross-platform
Python because the owner host is Windows without WSL. **Not executed here** —
this sandbox has no Docker engine (verified: `docker`/`docker compose` absent,
`command not found`).

**Safety remediation (same day, 2026-10-31).** The first revision of that
tool hardcoded the *production* project name (`mafhum-prod`, the `name:` in
`deploy/docker-compose.prod.yml`), so running it on the production host would
have stopped the production engine and force-deleted the production signature
volume. It was rewritten before any execution (it was never run anywhere) with
a verified-isolation architecture:

- every run generates a unique Compose project
  `mafhum-clamav-acc-<timestamp>-<nonce>`; `mafhum-prod`/`mafhum` (and any
  `mafhum-prod*` prefix) are reserved and rejected before any docker call;
- every compose command is issued through one wrapper that injects the
  isolated `--project-name` and refuses reserved names; images build under
  run-unique tags; the api is published on `127.0.0.1` only;
- a read-only preflight aborts (exit 2, nothing created) on project-name
  collision, existing containers/volumes carrying the run's label, or
  resource-name collisions;
- **the default run deletes no volume ever** — the isolated project's first
  `up` is the cold bootstrap, and cleanup is `down` without `-v`;
- the only deletion is the `--full` second bootstrap, which additionally
  requires `--confirm-isolated-test` and a triple gate (created by this run ∧
  ownership label matches the isolated project ∧ exact name re-verified
  immediately before `docker volume rm`);
- failure cleanup never deletes volumes (evidence preserved), and a post-run
  self-audit fails the run if any issued command could have addressed a
  production resource.

Pinned by `apps/api/tests/test_clamav_acceptance_safety.py` (17 tests, mocked
Docker CLI — no Docker or production environment needed), which also holds an
AST-level proof that no executable string in the tool names a production
resource. See `docs/DEPLOYMENT_RUNBOOK.md` §3 step 5 for the distinction
between CI engine tests, isolated local acceptance and production-host
acceptance.

## 6. Git: the PR stack landed, then protection

Executed per `docs/PR_MERGE_SEQUENCE_REVIEW.md` §3 (Option B, extended with
PR #8), preserving every PR's own merge record:

1. **PR #6 merged → `main`** (15 hardening commits, own merge commit).
2. **PR #7 merged → its verification base** (remediation D1–D4 preserved).
3. **PR #8 merged → its remediation base** (operational-review docs preserved).
4. **New PR `verification/operational-review-2026-10-31` → `main`** (the 7
   outstanding commits: report + 5 remediation + review docs), merged.
5. **This branch's PR → `main`** (the completion commits of this session).
6. **Branch protection on `main`: attempted, blocked by the credential.**
   Both `PUT /branches/main/protection` and `POST /rulesets` returned
   `403 Resource not accessible by integration` — the session's GitHub token
   can merge PRs but has no Administration write permission. `main` therefore
   remains unprotected, and enabling it is an owner action; the exact payload
   to apply (require PRs, the six always-running CI jobs as required status
   checks, no force-pushes, no deletions) is:
   `gh api -X PUT repos/Devmraustro/MAFHUM/branches/main/protection` with
   `required_status_checks.contexts` =
   `["Repository baseline", "Backend — lint, types, tests", "Security — SAST,
   secrets, config contract", "Local authenticated DAST (production
   posture)", "Migrations — upgrade and downgrade", "Frontend — lint, types,
   tests, build"]`, `strict=false`, `enforce_admins=false`,
   `allow_force_pushes=false`, `allow_deletions=false`. The path-filtered
   workflows (PPTX render, production malware acceptance) must NOT be made
   required, or PRs outside their paths could never merge.

Each commit landed exactly once; no rebase, no force-push, no branch deletion.

## 7. What still belongs to the owner (unchanged in nature, now one command each)

| # | Item | What exists now | What the owner must do |
|---|---|---|---|
| 1 | **Production ClamAV container evidence** (the top blocker) | `tools/clamav/production_bootstrap_acceptance.py` — the full gate in one command, isolated from production by construction (v2, see §5) | run it on a Docker host with a real env file (it never touches `mafhum-prod`); attach the timed report from `.clamav-acceptance/<project>/report.md` |
| 2 | **Live alert delivery** | validated configs + CI gate + `--live` contract check | supply `deploy/secrets/metrics_token`, a real Alertmanager destination, start `docker-compose.monitoring.yml`, fire one rehearsal alert and prove receipt |
| 3 | **PowerPoint visual parity** | OOXML/RTL/benchmark gates green in CI (`pptx-render.yml` does headless LibreOffice) | open the benchmark decks in real PowerPoint on Windows and confirm appearance |
| 4 | **Staging DAST (full claim)** | local DAST executed + in CI; scope table rehearsed | authorize + run ZAP baseline and authenticated scan against a real staging stack with a real engine and real TLS |
| 5 | **Deployment decisions** | `docs/FULL_STACK_DEPLOYMENT.md`, runbook, rollback guide | domain, secrets, retention, KMS, digest pinning, licence, visibility |
| 6 | **Branch protection on `main`** | exact API payload prepared (§6) | enable it with an owner credential that has Administration write |

**Production-readiness statement:** MAFHUM remains **not production-ready**
until item 1 (and then 2–4) is executed by the owner. Everything executable
from this environment has been executed and recorded.
