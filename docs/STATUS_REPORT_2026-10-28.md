# MAFHUM — status report, 2026-10-28

Branch: `arena/7a7fdcba-mafhum` (based on `main` `2a15f0c`) · Review PR: **#6**
(not merged) · Repository visibility, permissions, the public Vercel frontend and
the absence of a `LICENSE` are **unchanged**.

**Overall status: Incomplete.** Phases 0–7 are implemented; the public website
exists but the public API does not. Essential security and product work remains,
so this report does not say "complete with blockers".

---

## 1. Task matrix

Legend: **DONE** = implemented and verified by a test that ran · **PARTIAL** =
implemented but a stated part is design-only or unverified on a real host ·
**BLOCKED** = cannot be executed in this environment.

| # | Task | Status | Verification / precise limitation |
|---|---|---|---|
| 1 | Production ClamAV wiring + real fail-closed acceptance | **DONE** (image runtime on a real host still open) | `deploy/clamd/` image **built** on CI and its clamd binary/config smoke-tested; healthcheck performs a real `clamdscan`; `docker compose config` + contract pass; `production-malware-acceptance.yml` run `40620251216` verified clean/EICAR/outage against a **real** signature-loaded clamd (all 11 core steps success). **CI did not use that container as the daemon** — its in-container signature load did not reach SCAN-ready within a bounded window, so starting the production image and proving its healthcheck on a real host is an explicit operator / Z Code task. |
| 2 | Account export with ownership isolation + bounded streaming | **DONE** | `apps/api/tests/test_account_export.py` (11 tests): cross-user isolation, safe ZIP names, file-count/byte caps, audit row, unauthenticated 401. |
| 3 | Configurable audit retention + privacy docs | **DONE** | `test_audit_retention.py` (8 tests): 0-days no-op, dry run, cutoff, batch cap, other tables untouched, CLI defaults. `DATA_POLICY.md` §8. Purge is opt-in and never scheduled. |
| 4 | At-rest encryption ADR + testable interfaces | **PARTIAL — by design** | ADR 0006 + `core/crypto.py` + `test_field_encryption.py` (14 tests) with synthetic keys. **No production column is encrypted, no KMS, no key material.** A guard test asserts the module is not wired in. |
| 5 | Scoped metrics token, Prometheus config, alert rules | **PARTIAL — templates only** | `test_metrics_token.py` (9 tests), contract check that every alert-rule metric is emitted. **No monitoring stack is deployed and `alertmanager.yml` has no destination** — deliberately, rather than shipping a fake webhook. |
| 6 | Security CI + pentest readiness | **PARTIAL** | Bandit, `security_baseline.py`, compose contract all pass in the `security` CI job. `PENTEST_READINESS.md` written. **No DAST/ZAP/pentest/container-image scan has run** — needs a live target and human triage. |
| 7 | Headless LibreOffice PPTX render + Arabic/RTL checks | **DONE** (headless only) | `pptx-render.yml` runs **40614116944**, **40620251067**: all 15 steps success including "Convert both decks to PDF with headless LibreOffice" and "Rasterise and verify every page". **PowerPoint visual parity is BLOCKED** (Windows/manual). |
| 8 | Windows backup/restore URL fix + Windows setup docs | **DONE in code, BLOCKED on Windows** | `test_database_url_portability.py` (12 tests) covers POSIX socket, Windows TCP, IPv6 and encoded-credential URLs; full `test_backup_restore.py` passes locally. **No native Windows host ran the suite.** |
| 9 | Evidence-backed stale doc corrections | **DONE** | 15+ files corrected; superseded banners on historical reports; new §15 in `STATUS_REPORT_2026-10-27.md`; no remaining "Phases 4–7 not started / 435 tests / no bulk export / socket client not written / detection not verified" claims. |
| 10 | Production Compose contract validation + runbook updates | **DONE** | `prod_compose_contract.py` (3 pytest tests) + CI job **40620251216** "Compose config + production contract" ran `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod.example config --quiet` successfully. Runbooks list exact operator inputs. |

---

## 2. Exact local evidence

All commands run from `/home/user/MAFHUM` (or the noted subdirectory) on
2026-10-28. Python 3.11.2 in `/tmp/mafhum-venv`; Node v22.22.3 / npm 10.9.8.

| Command | Exit | Result |
|---|---|---|
| `ruff check .` (apps/api) | 0 | All checks passed |
| `ruff format --check .` (apps/api) | 0 | 136 files already formatted |
| `mypy --no-incremental src` (apps/api) | 0 | Success: no issues found in 72 source files |
| `pytest -p no:cacheprovider -ra` (apps/api) | 0 | **961 passed, 5 skipped in 201.62 s** |
| `pip-audit` | 0 | No known vulnerabilities (`mafhum-api` skipped: local editable) |
| `bandit -r apps/api/src -ll -q` | 0 | no medium/high findings |
| `python tools/security_baseline.py` | 0 | ALL SECURITY BASELINE CHECKS PASSED |
| `python tools/acceptance/prod_compose_contract.py` | 0 | ALL PRODUCTION COMPOSE CONTRACT CHECKS PASSED |
| `ruff check --config apps/api/pyproject.toml tools/security_baseline.py tools/acceptance/` | 0 | pass |
| `npm ci` / `npm run typecheck` / `npm run lint` / `npm test` / `npm run build` (apps/web) | 0 | 107/107 tests; build OK |
| `npm audit --omit=dev` (apps/web) | 0 | 0 vulnerabilities |
| `npm ci` / `npm test` / `npm audit` (apps/slides) | 0 | 2/2 tests; 0 vulnerabilities |
| `python apps/api/tools/benchmark_pptx.py` | 0 | both generators: 6 slides, chart+table, editable, Arabic+French preserved |
| `check_pptx_visual.py --rtl` (both decks) | 0 | `ok=true`, `render_check: NOT PERFORMED` |
| `pytest tests/test_pptx_render_check.py` | 0 | 10/10 synthetic PGM/text checks |
| YAML/JSON parse of all workflows + `deploy/**` | 0 | 0 failures |

The 5 pytest skips are environmental and disclosed: one shallow-clone digest
test, one live-research integration, one live-source retrieval, two live egress
checks. None is a silently disabled security test.

**BLOCKED locally:** `docker compose config` (no `docker` binary), real ClamAV
engine (no ClamAV), LibreOffice/PowerPoint (no renderer), native Windows run,
GitHub Actions step logs (results blob host is not reachable from this sandbox —
step-level conclusions were read from the GitHub API instead).

### Commits on the review branch

```
1baa884  feat(security): real production ClamAV wiring, fail-closed config and acceptance gates
0ba347a  feat(privacy): bounded account export, audit retention, encryption ADR and AEAD interfaces
5220aed  feat(monitoring,security): scoped metrics token, Prometheus/alert config, CI security gate
60cd579  feat(pptx,windows,docs): headless render gate, Windows URL portability, stale-doc corrections and deployment contract docs
d90e600  docs: final contradiction sweep (engine bundling, LibreOffice scope, encryption status)
95ca156  ci(pptx): avoid SIGPIPE failures when reporting renderer/font versions
8481366  ci(malware): probe readiness with a real SCAN and emit API-readable diagnostics
3514f53  ci(malware): fix pipefail false-positive container-exit check
3013ba9  ci(malware): seed signatures on host, bind-mount into the production clamd image
c64b701  fix(clamd): use a real log file mirrored to stderr; prevent /dev/stdout ELOOP regression
3b22371  ci(clamd): bound every SCAN probe with timeout; cancel superseded engine runs
eb23ef2  ci(malware): use the proven host clamd daemon for real-engine fail-closed acceptance
bc79f02  ci(malware): verify real-engine EICAR through MAFHUM's own clamd client
6610a14  docs: record final CI evidence and the production clamd image runtime limitation
```

Final documentation-only commits may follow this line, so the exact review tip
is `git rev-parse HEAD` on `arena/7a7fdcba-mafhum`. Earlier failed runs
(`40614116773`, `40615090587`, `40615509350`, `40616284837`, `40616781698`,
`40617850330`, `40619361825`) are retained as evidence of the iterative fixes;
they are not the final state.

### GitHub Actions evidence (PR #6)

| Workflow | Run | Result |
|---|---|---|
| MAFHUM CI (5 jobs incl. new `security`) | **`40620251266`** | success, 3 m 1 s — repository baseline, backend, **security**, migrations, frontend all pass |
| PPTX render — headless LibreOffice | **`40620251067`** | success, 49 s — 15/15 steps, including PDF conversion, rasterisation and verification |
| Production malware wiring | **`40620251216`** | success, 1 m 42 s — `contract` job (`docker compose config` + contract) **and** `fail-closed` job (real signatures, engine-level EICAR, production image build, two API instances, clean/EICAR/outage acceptance) all green |
| Vercel | preview deployment | pass — public frontend unchanged |

Step-level evidence for the final malware run (read from the GitHub Jobs API
because the raw log blob host is unreachable from this sandbox): all 11 core
steps `success`, including "Install ClamAV, refresh real signatures, start a
real daemon", "Build and smoke-test the production ClamAV image", "Engine-level
EICAR detection", "Start one API with the engine and one with a dead engine
port" and "Real fail-closed acceptance". The only annotation is a Node.js 20
action-runtime deprecation warning, not a failure.

Workflow URL pattern:
`https://github.com/Devmraustro/MAFHUM/actions/runs/<run-id>`.
PR: `https://github.com/Devmraustro/MAFHUM/pull/6`.

---

## 3. Remaining security risks

1. **No public API, no production deployment.** All production behaviour is
   evidenced only on CI runners.
2. **ClamAV signature freshness is not monitored.** The `malware_scanner`
   readiness gauge proves a PING answer, not that signatures are current. A
   mirror outage after a healthy start would leave a stale database.
   Additionally, the **production `deploy/clamd` image was built and its binary
   smoke-tested but never observed reaching SCAN-ready inside a container on
   CI**; its daemon startup and healthcheck must be proven on the owner's host
   (Z Code §B5). The in-container `freshclam` bootstrap was found to be slow and
   fragile under CI; the acceptance workflow therefore uses a host daemon with
   the same engine and signatures.
3. **No application-layer encryption at rest.** A database dump or blob volume
   exposes user content. ADR 0006 is design + synthetic-key tests only; the KMS,
   key escrow and rotation policy are owner decisions.
4. **No alert delivery.** `alertmanager.yml` has an `unconfigured` receiver.
   Alerts are visible in the UI but reach nobody.
5. **No DAST, pentest, fuzzing or container-image scanning.** Static gates find
   bug classes, not exploitable instances.
6. **No volume/disk encryption, backup encryption, log retention or dashboard.**
7. **Backups are not purged on account deletion** (documented residual data).
8. **OCR, AI provider and live-source content provenance are out of scope.**
   TLS connectivity is not provenance.
9. **Repo was last observed public** while the owner wants private source. No
   visibility/permission change was made; collaborators, CI and Vercel impact
   must be assessed before any change.
10. **No `LICENSE`** — an open owner decision.
11. **PowerPoint visual parity is unverified**; headless LibreOffice is a proxy,
   not the same layout engine.
12. **The ClamAV image and monitoring image tags are not digest-pinned.** The
   operator must verify and pin them before production.

---

## 4. External prerequisites (owner)

Hosting provider with Docker Compose v2 and persistent volumes; domain + DNS
A/AAAA; `MAFHUM_DOMAIN`, `MAFHUM_ACME_EMAIL`; `POSTGRES_USER/PASSWORD/DB`,
`SESSION_SECRET`, `ALLOWED_ORIGINS`; a **separate** `METRICS_SCRAPE_TOKEN`; a
ClamAV image digest; an alert destination and its credentials; an approved
audit/backup retention policy (and legal-hold ruling); a KMS/HSM and encryption
scope decision; build-time `MAFHUM_API_URL`; visibility and licence decisions;
explicit authorisation for production deployment, a Vercel rebuild, a DNS change,
a pentest and the PR merge.

See `docs/Z_CODE_HANDOFF_2026-10-28.md` for the narrow prompt covering only the
work that genuinely needs a local Windows machine, local services, interactive
tools or owner-provided resources.
