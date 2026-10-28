# Z Code handoff (narrow scope) — 2026-10-28

The prompt below is deliberately limited to work that **cannot** be done on the
Arena/Linux CI branch: a native Windows machine, locally running services,
interactive desktop/proxy tools, or owner-provided credentials and decisions.
Everything else (code, tests, CI, docs) is already on PR
`https://github.com/Devmraustro/MAFHUM/pull/6` — do not re-implement it.

---

```
MAFHUM — Local / Windows / Owner Verification Pass (narrow scope)

CONTEXT
Repository: Devmraustro/MAFHUM. Review branch `arena/7a7fdcba-mafhum`,
PR #6 (NOT merged), base `main` = 2a15f0c. Phases 0–7 are implemented. CI is
green on the PR, including a real headless LibreOffice PPTX render and a
production Compose `docker compose config` contract. The public website is a
frontend-only Vercel rehearsal; there is NO public API and NO production
deployment.

A separate coding agent already implemented and tested: production ClamAV
wiring + fail-closed acceptance, bounded account export, audit retention, an
encryption ADR (design only, NOT enabled), a scoped metrics token with
Prometheus/alert templates, a security CI gate, a headless LibreOffice render
gate, Windows-safe database URLs, stale-doc corrections and a Compose contract
validator. DO NOT redo any of that work.

READ FIRST (do not modify):
  docs/STATUS_REPORT_2026-10-28.md, docs/Z_CODE_HANDOFF_2026-10-28.md,
  docs/WINDOWS_SETUP.md, docs/PENTEST_READINESS.md, docs/MONITORING.md,
  docs/DEPLOYMENT_RUNBOOK.md, docs/FULL_STACK_DEPLOYMENT.md,
  docs/ROLLBACK_GUIDE.md, deploy/docker-compose.prod.yml,
  deploy/clamd/{Dockerfile,clamd.conf,healthcheck.sh,entrypoint.sh},
  deploy/prometheus/alertmanager.yml,
  .github/workflows/production-malware-acceptance.yml,
  apps/api/tests/test_database_url_portability.py,
  apps/api/tests/test_backup_restore.py,
  apps/api/tools/check_pptx_render.py.

YOUR JOB IS ONLY THE FOLLOWING. Report anything you cannot run as BLOCKED with
the exact reason; never substitute a mock for a real service.

A. NATIVE WINDOWS VERIFICATION (needs a real Windows 10/11 machine)
1. Do a clean-room walkthrough of docs/WINDOWS_SETUP.md using PowerShell,
   Python 3.11/3.12, Git and (optionally) Docker Desktop with WSL2. Record every
   command you actually ran and its exit code. List every place the document is
   wrong or ambiguous and propose a precise correction.
2. Run `python -m pytest -q tests/test_database_url_portability.py
   tests/test_backup_restore.py -rf` from apps/api with
   PYTHONPATH="src;.". Confirm the portable URL helper works with the URL shape
   pgserver actually returns on Windows, and record that shape (redact secrets).
   Then run the FULL backend suite and report the exact summary line.
3. Run the backup/restore drill end to end on Windows. Record
   `pg_dump --version` and `pg_restore --version` and confirm their major version
   matches the pgserver cluster. If they mismatch, record the exact failure and
   the resolved PATH fix; do not silently use mismatched binaries.
4. Open BOTH generated decks (artifacts/pptx-benchmark/benchmark_python.pptx
   and benchmark_pptxgenjs.pptx) in real Microsoft PowerPoint. Check Arabic
   shaping, RTL paragraph direction, no tofu/missing-glyph boxes, mixed
   Arabic/French citation order, no clipped or overlapping text, 16:9 slide
   size, chart labels, bibliography table column order, and that text boxes are
   genuinely editable. Capture screenshots with slide numbers. The CI
   LibreOffice render is NOT this check. Report PASS or a precise defect list.

B. LOCAL SERVICES / INTERACTIVE TOOLS (needs a real Docker host + human)
5. On a host you control, bring up deploy/docker-compose.prod.yml with a REAL
   domain and owner-supplied secrets. Confirm Caddy obtains a real certificate,
   `/health/ready` reports database=ok, storage=ok, malware_scanner=ok, and the
   clamd container becomes healthy with a downloaded signature set. Record the
   engine version, signature file timestamps and container health status.
   IMPORTANT: CI built and smoke-tested deploy/clamd/ but never observed that
   container reaching SCAN-ready — its in-container `freshclam` bootstrap was
   slow/fragile under CI, so the acceptance workflow used a host-installed clamd
   with the same engine and signatures. Proving the production image's own
   bootstrap, config, healthcheck and 12-hourly refresh on a real host is a
   mandatory, unverified check. If it fails, capture `docker logs`, the
   container's `.State` and the `/tmp/mafhum-clamd.log` contents.
6. On that host run the three-case malware acceptance with real signatures:
   clean file accepted; EICAR DOCX from apps/api/tools/eicar_probe.py rejected
   with details.reason=malware_detected; clamd stopped -> upload rejected with a
   scan_* reason, never CLEAN and never malware_detected. Then verify signature
   refresh after 24 h. Do NOT claim detection from a configuration flag.
7. Start deploy/docker-compose.monitoring.yml with the real
   METRICS_SCRAPE_TOKEN in deploy/secrets/metrics_token (mode 0600). Verify
   Prometheus scrapes /api/v1/admin/metrics, the token is rejected on
   /admin/overview, and each alert rule can be fired. Add the owner's REAL alert
   destination to alertmanager.yml and PROVE a test alert is delivered (email or
   webhook receipt). A green `amtool check-config` is not proof of delivery.
8. If the owner explicitly authorises it in writing, run an interactive
   authenticated DAST (OWASP ZAP or Burp) against this LOCAL stack only,
   following docs/PENTEST_READINESS.md §3–§7. Triage findings yourself. Report
   reproducible high/medium findings with redacted request/response evidence and
   a CVSS vector. Never DAST production or a third party. If not authorised,
   write "NOT PERFORMED — no authorisation".

C. OWNER DECISIONS / CREDENTIALS (never do these autonomously)
9. Ask the owner for, and only then act on: production secrets; domain/DNS
   control; alert destination; audit and backup retention policy and a
   legal-hold ruling; a KMS/HSM choice before enabling any field encryption
   (docs/ADR/0006-encryption-at-rest.md is Proposed, not Accepted); image
   digest pinning for the ClamAV, Prometheus and Alertmanager images; the
   build-time MAFHUM_API_URL / Vercel rebuild decision; the repository
   visibility decision (last observed PUBLIC — before changing it, list the
   collaborators, CI workflows and Vercel project that would be affected and
   obtain written confirmation); the licence decision (currently NO LICENSE);
   and explicit authorisation before any production deployment, DNS change,
   Vercel rebuild, PR merge or pentest.
10. Only with that authorisation, follow docs/DEPLOYMENT_RUNBOOK.md §3–§5
    exactly: validate `docker compose config` + prod_compose_contract.py +
    security_baseline.py with the real (uncommitted) env file, deploy, verify
    readiness and the malware gate, take a quiesced backup, restore it into a
    scratch database, and rehearse docs/ROLLBACK_GUIDE.md. Record the exact
    deployed commit SHA.

HARD RULES
- Never commit directly to `main`; never force-push; never rewrite history;
  never discard another agent's changes; never merge PR #6 without explicit
  owner authorisation.
- Never add a LICENSE, never change GitHub visibility/permissions without
  written owner approval, never hide the public website to "protect" source.
- Never expose, log or paste secrets, tokens, passwords, private keys, webhook
  URLs or full connection strings — not in a report, a screenshot or a commit.
- Never claim a capability exists because a flag, interface or placeholder
  exists. Prove it with a command, a response and a screenshot where relevant.
- Do not call TLS connectivity proof of publisher-content provenance. Do not
  treat structural PPTX checks or a LibreOffice PDF as PowerPoint visual parity.
- Do not implement OCR, an AI provider or live-source retrieval without a
  separate scope decision. Keep OCR disabled until a real engine and an Arabic
  round-trip test exist.
- Do not implement a fake alert destination, a fake KMS or a fake scan result.
- Record for every check: exact command, exit code, tool/engine version, commit
  SHA and any workflow URL. If a check cannot run, write BLOCKED with the precise
  reason. Do not silently skip.

DELIVERABLE
One English markdown report (docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md on a
NEW dedicated branch, not `main`) with sections A–C, a PASS/FAIL/BLOCKED table,
screenshots where meaningful, exact console evidence (secrets redacted), the
committed SHA tested, and a short list of concrete defects for the other agent
to fix. End with an explicit statement of what is still NOT production-ready.
Do not declare the project complete.
```
