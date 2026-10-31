# Operational verification report — 2026-10-31

Executor: Z Code (native Windows host) · Base commit tested:
`9882099df4c62c44aeceedca9caa5060f1256e70` (branch
`remediation/windows-findings-d1-d4`, PR #7 head) · Session window began
2026-10-31 00:50 +01:00 · Companion analysis: `docs/PR_MERGE_SEQUENCE_REVIEW.md`.

**Headline: PHASE 2 (real production ClamAV container) remains BLOCKED — no
Docker engine exists on this host, so the single most important operational
gate still has no container-runtime evidence anywhere.** Phases 1, 3 and 5
produced real, recorded results; Phase 4 remains blocked by absent software.
MAFHUM is **not production-ready**, and nothing in this report claims
otherwise.

## 0. Task matrix

| Phase | Task | Status |
|---|---|---|
| 1 | Git topology & merge-readiness review | **PASS** (analysis only; `docs/PR_MERGE_SEQUENCE_REVIEW.md`) |
| 2A | ClamAV image build, freshclam bootstrap, SCAN-READY timing | **BLOCKED** — no Docker |
| 2B | Clean / EICAR-DOCX / fail-closed acceptance on the production image | **BLOCKED** — no Docker |
| 2C | Container restart, signature persistence, empty-volume bootstrap, refresh | **BLOCKED** — no Docker |
| 3 | Monitoring configuration audit | **PASS** (config + code audit; live delivery stays an owner task) |
| 4 | PowerPoint visual verification | **BLOCKED** — PowerPoint not installed (re-verified) |
| 5 | Authenticated DAST preparation | **PARTIAL** — procedure prepared; **NOT EXECUTED** (no authorisation) |

---

## 1. PHASE 1 — Git topology and merge-readiness (PASS)

Full analysis in `docs/PR_MERGE_SEQUENCE_REVIEW.md`. Summary of measured facts:

* Strictly linear stack: `main` `2a15f0c` ← PR #6 head `1199178` (15 commits)
  ← verification `26e83d4` (report) ← PR #7 head `9882099` (5 remediation
  commits). Every merge-base with `main` equals `main`'s own tip, so all three
  merges are conflict-free and order-determined.
* PR #7 contains PR #6's changes **by ancestry only**; its diff is the five
  remediation commits.
* Recommended sequence (no base change): **merge #6 → merge #7 (into its
  verification base) → open + merge one new PR `verification… → main`**
  carrying report + remediation (6 commits). Never merge the verification PR
  into `main` before PR #6 — GitHub would auto-mark PR #6 merged and destroy
  its review record.
* Branch protection: `GET /branches/main/protection` → **404 "Branch not
  protected"**; `GET /rules/branches/main` → no rulesets. The credential used
  for the read has admin rights. Enabling protection + required checks is
  recommended before the sequence executes (owner decision, not performed).
* Nothing was merged, retargeted, rebased or force-pushed.

## 2. PHASE 2 — Real production ClamAV container (BLOCKED)

### 2.1 Environment re-check (2026-10-31 00:50–00:55 +01:00)

| Check | Command | Result |
|---|---|---|
| Docker CLI | `docker --version` | `command not found` (bash exit 127) |
| Docker Engine | `docker info` | not reachable — CLI absent |
| Compose v2 | `docker compose version` / `docker-compose --version` | both `command not found` (exit 127) |
| Docker Desktop install | `ls "C:\Program Files\Docker\Docker\resources\bin"`; `ls "$LOCALAPPDATA/Docker"` | both absent |
| WSL2 | `wsl -l -v` | "Le Sous-système Windows pour Linux n'est pas installé" (WSL not installed) |
| Disk space | `df -h /c` | 237 G total, **117 G free** (sufficient — irrelevant without an engine) |
| Existing MAFHUM containers/volumes | n/a | none exist (no engine); nothing to preserve or delete |

**BLOCKED reason (exact):** this Windows host has no Docker CLI, no Docker
Engine, no Docker Compose v2 and no WSL2. Building
`deploy/clamd/Dockerfile` (`mafhum-clamd:0.3.0`) and starting the production
stack is impossible here. Per the standing rule, CI green is **not** container
evidence, so no bootstrap, SCAN-READY, acceptance or recovery claim is made.

### 2.2 What CI does prove on the tested commit (not a substitute)

GitHub Actions on `9882099df4c62…` (queried via API, `head_sha=` filter):
*Production malware wiring — real fail-closed acceptance*: **success**;
*MAFHUM CI*: **success**. As documented since 2026-10-28, CI uses a host clamd
rather than the production image's own in-container bootstrap, so the image's
runtime behaviour remains unproven on ANY host.

### 2.3 Exact runbook for the host that has Docker (owner or future session)

Files inspected (unchanged since the 2026-10-30 review; see
`docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md` §4.2 for the line-by-line reading):
`deploy/clamd/{Dockerfile,entrypoint.sh,healthcheck.sh,clamd.conf,clamdscan.conf}`,
`deploy/docker-compose.prod.yml`, `.github/workflows/production-malware-acceptance.yml`.
The one-time Windows hazard in those files (CRLF) was fixed by commit `00cbdd5`
(`.gitattributes`), so a Windows-built checkout now produces runnable scripts.

To be executed on a Docker-capable host, recording time-to-SCAN-READY at each
step (synthetic files only; EICAR test string only; never real malware):

1. `docker compose -f deploy/docker-compose.prod.yml build clamd` — record
   build time and image ID.
2. `docker compose -f deploy/docker-compose.prod.yml up -d clamd` with an
   **empty** `clamd_db` volume; poll `docker inspect --format
   '{{.State.Health.Status}}' mafhum-prod-clamd-1` every 15 s; record
   container-start → first `healthy` (the healthcheck performs a real bounded
   `clamdscan`, so "healthy" == SCAN-READY) and the `freshclam` download size
   from `docker logs`.
3. Three-case acceptance through a running `api` service (clean DOCX accepted;
   `apps/api/tools/eicar_probe.py` DOCX rejected with
   `details.reason=malware_detected`; `docker stop` the clamd container →
   upload rejected with a `scan_*` reason, never CLEAN, never
   `malware_detected`; API and worker must refuse to mark scanning operational
   while the engine is down).
4. Recovery: `docker start` the container — signature volume must be reused
   (no re-download; check log line "reusing the signature database on the
   volume") and health must return without a fresh bootstrap; then delete the
   volume and repeat step 2 to prove a cold bootstrap; confirm the 43 200 s
   (12 h) refresh loop from the entrypoint log timestamps.

## 3. PHASE 3 — Monitoring configuration audit (PASS, config-level)

Verified on the tested commit, all commands exit 0:

* **YAML validity:** `prometheus.yml`, `alert.rules.yml`, `alertmanager.yml`
  all parse (PyYAML `safe_load`). 3 rule groups / **7 rules**. (`promtool check
  rules/config` was NOT run — no Prometheus binaries exist on this host; that
  check remains for the monitoring host.)
* **Rules ↔ metrics contract:** every metric referenced by the 7 rules
  (`up`, `mafhum_malware_scan_required`, `mafhum_malware_scanner_available`,
  `mafhum_audit_denied_total`, `mafhum_processing_pending`,
  `mafhum_documents_total{status="failed"}`, `mafhum_audit_events_total`) is
  emitted by `apps/api/src/mafhum_api/api/v1/admin.py` — verified by reading
  the exporter source; no rule references a non-existent series.
* **Scrape configuration:** internal-network target `api:8000`, path
  `/api/v1/admin/metrics`, `bearer_token_file: /run/secrets/
  mafhum_metrics_token`, 30 s intervals, self-scrape job for monitor-death
  detection. Template only — inactive until
  `deploy/docker-compose.monitoring.yml` is started with a real token file.
* **Bearer-token handling (code level):** `api/deps.py::metrics_access` —
  `Authorization: Bearer` is compared with `hmac.compare_digest`
  (constant-time); a bearer request **never** falls back to a cookie session
  (wrong token fails loudly); **no token accepted when the setting is empty**
  (no accidental backdoor); the token grants access to `/admin/metrics` only.
  `tests/test_metrics_token.py`: **7 passed** locally on this host.
* **Alertmanager:** receiver `unconfigured` with NO destination — deliberately;
  grouping (30 s wait / 5 m interval / 4 h repeat), severity routing and an
  inhibit rule (`MafhumApiDown` suppresses warning noise) are correct. **No
  fake webhook or placeholder is presented as delivery** — nothing is
  delivered today, and this report claims no delivery.
* Owner inputs required before live delivery (none exist in the repo, by
  design): a real `METRICS_SCRAPE_TOKEN` (≥32 chars, production-enforced) as
  `deploy/secrets/metrics_token` mode 0600; a real alert destination (SMTP
  smarthost + credentials, or an owner-controlled webhook URL) added to
  `deploy/prometheus/alertmanager.yml`; a monitoring host with Docker; then
  the §2-style evidence: scrape `up`, one deliberately fired rule, and proof
  of receipt at the destination.

## 4. PHASE 4 — PowerPoint verification (BLOCKED)

Re-checked 2026-10-31: no `POWERPNT.EXE` App-Paths registry key, no
`C:\Program Files\Microsoft Office`, no LibreOffice either. Visual inspection
of Arabic shaping, RTL direction, mixed Arabic/French text, charts, tables,
clipping, font fallback and editability **cannot be performed on this host**.
No LibreOffice substitute was used or claimed. The CI gate
(`.github/workflows/pptx-render.yml`: headless LibreOffice → PDF → rasterised
page verification) remains CI-scope only. The structural evidence from
2026-10-30 (`benchmark_pptx.py` regeneration + `check_pptx_visual.py --rtl`
both `ok=true`) still stands for deck integrity, not for PowerPoint parity.

## 5. PHASE 5 — Security testing boundary (PARTIAL: prepared, NOT executed)

`docs/PENTEST_READINESS.md` re-reviewed; rules of engagement unchanged. The
local authenticated DAST procedure is now concrete and ready, and **was not
executed**: no written owner authorisation naming host, window and tester
exists. When authorised, the procedure is:

1. Target: a **local** `deploy/docker-compose.prod.yml` stack with synthetic
   data only (`MAFHUM_DOMAIN=localhost` rehearsal variant — never the public
   Vercel site, never production, never third parties).
2. Tooling: OWASP ZAP baseline + authenticated scan; session established by
   driving `/api/v1/auth/login` and reusing the cookie jar; CSRF/Origin
   behaviour per `docs/THREAT_MODEL.md`.
3. Scope: exactly the table in `PENTEST_READINESS.md` §4 (auth, session/CSRF,
   authorisation isolation, admin+audit, scoped metrics, upload validation,
   malware policy with EICAR only, export isolation, deletion, TLS/headers).
4. Rate limits: the documented login throttle is not to be brute-forced; no
   DoS, no destructive load.
5. Triage: findings recorded with redacted request/response evidence and a
   CVSS vector; stop condition per §3 of the readiness document.

## 6. Evidence index (exact commands, exit codes)

| # | Command (abbreviated where interactive) | Exit | Note |
|---|---|---|---|
| 1 | `git fetch origin` (after 1 failed attempt, retried) | 0 | remote tips listed in the merge review |
| 2 | `git merge-base` ×4, `git log main..branch` ×3 | 0 | §1 / merge review doc |
| 3 | `curl /branches/main/protection` (admin token) | HTTP 404 "Branch not protected" | no protection on `main` |
| 4 | `curl /rules/branches/main` (admin token) | HTTP 200, `[]` | no rulesets |
| 5 | `docker --version` / `docker compose version` / `docker info` | 127 | CLI absent → Phase 2 BLOCKED |
| 6 | `wsl -l -v` | n/a (localized "not installed") | no WSL2 |
| 7 | `df -h /c` | 0 | 117 GB free |
| 8 | PyYAML `safe_load` of the 3 monitoring files | 0 | all parse; 7 rules |
| 9 | `pytest -q tests/test_metrics_token.py` | 0 | 7 passed |
| 10 | `reg query …POWERPNT.EXE` / `ls "C:\Program Files\Microsoft Office"` | key/dir not found | Phase 4 BLOCKED |
| 11 | API `actions/runs?head_sha=9882099…` | 0 | both workflows success on the tested commit |

## 7. Production-readiness statement

**MAFHUM is not production-ready.** The blocking gates, in priority order:
(1) production ClamAV container bootstrap → SCAN-READY → three-case
acceptance → recovery on a real Docker host (§2 — still no evidence on any
host); (2) real alert delivery with owner credentials (§3); (3) PowerPoint
visual parity on a machine that has PowerPoint (§4); (4) an authorised DAST
pass (§5); plus the owner decisions listed in
`docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md` §9 (secrets, domain, retention,
KMS, digest pinning, licence, visibility) and the branch-protection hardening
recommended in `docs/PR_MERGE_SEQUENCE_REVIEW.md` §4. This report intentionally
does not declare completion.
