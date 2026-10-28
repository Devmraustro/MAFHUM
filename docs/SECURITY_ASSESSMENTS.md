# Security assessments

Point-in-time assessments of security capabilities MAFHUM does **not** have, and
of the deployment tooling that could not be exercised. Every result below was
produced by running a command in the development sandbox; nothing here is
inferred from documentation.

Sandbox: Debian 12, Python 3.11.2, Node v22.22.3, PostgreSQL 16.2 (via
`pgserver`), 2 CPUs, 3939 MB RAM.

---

## 1. Malware scanning — INTEGRATION IMPLEMENTED, DETECTION VERIFIED IN ACCEPTANCE

**Status change, 2026-10-26:** the staging acceptance gate (Gate A) ran against a
real ClamAV daemon with fresh signatures on GitHub's runners and the real engine
detected the EICAR test payload inside an uploaded document, which the API — with
`MALWARE_SCAN_REQUIRED=true` — rejected with `422 {"reason":"malware_detected"}`.
Evidence: staging run 40377229156 on `3589dc8` (all three gates green), including
the engine-level `clamdscan` verdict on the exact artifact uploaded. What is
verified is the acceptance environment: a real engine, fresh signatures, real
rejection. What is still true: **a default install configures no engine and scans
nothing** — `malware_scanning_available` discloses the live state, and detection
remains the operator's claim once they provision their own `clamd`.

What changed on 2026-10-23: the code can now scan. `services/documents/clamd.py`
speaks the clamd `zINSTREAM` socket protocol, so an operator who points
`clamd_unix_path` or `clamd_host` at a running `clamd` gets real scanning, with the
verdict persisted on the document and `malware_scan_required` able to fail the
upload closed. A default install still configures no engine and still scans nothing.

The distinction to hold on to: **the protocol, the fail-closed policy and real
engine detection in the acceptance environment are verified; no particular
deployment is verified until its own engine is probed.** The development sandbox
has no ClamAV, but GitHub-hosted runners do: Gate A installs `clamav-daemon`,
refreshes signatures with `freshclam`, starts a real `clamd`, confirms the EICAR
verdict at engine level and then confirms the API rejects the upload. Since
2026-10-28 the production Compose blueprint also ships a real ClamAV service
(`deploy/clamd/`) with a SCAN-based healthcheck, and
`.github/workflows/production-malware-acceptance.yml` exercises clean, EICAR and
engine-outage cases against it. "This deployment detects malware" still requires
the operator to verify *their* engine and signature freshness.

### What the system actually does

Upload validation in `apps/api/src/mafhum_api/services/documents/validators.py`
checks magic bytes, extension, size, page count, archive entry count,
decompressed size and compression ratio. **None of that is malware scanning.**
It rejects malformed and abusive files; it detects no malicious content. A
correctly-formed PDF carrying an embedded exploit passes every one of those
checks.

Compensating controls that do exist:

| Control | Effect |
| --- | --- |
| Private blob storage, `0700`/`0600`, outside any static directory | The API never serves files from disk directly |
| Authorization on every read, `AuthorizationError` indistinguishable from not-found | No download before authorization; no existence probing |
| Text extracted and rendered as **text only** | Extracted content is not executed or interpreted as markup |
| Parser and size limits | Bounds decompression bombs and pathological inputs |
| Post-commit blob deletion with orphan reconciliation | No bytes outlive a rolled-back delete |

### Disclosure

The absence is stated by the code, not merely implied by a gap:

- `services/documents/scanning.py` defines `MalwareScanner`, `ScanVerdict` and
  `NullScanner`. `NullScanner.available` is `False` and `scan()` can only return
  `NOT_SCANNED` — there is no code path that yields `CLEAN`.
- `GET /api/v1/documents/limits` returns `malware_scanning_available: false`,
  derived from the live scanner object rather than a literal, so the disclosure
  cannot drift from the behaviour.
- The upload card renders that limitation in Arabic and French.
- `tests/test_malware_scanning.py` (19 tests) asserts an unscanned upload can
  never be reported clean, and that `requires_clean()` rejects `NOT_SCANNED` and
  `ERROR` as firmly as `INFECTED`. The tests drive a stub `clamd` over a real Unix
  socket, so the protocol implementation is exercised for real: clean, infected,
  daemon error, an unrecognised reply, an unreachable daemon, a hung daemon and a
  daemon that vanishes mid-scan are each asserted to map to the right verdict, and
  the two that must not be read as "clean" are asserted not to be.

### Options evaluated

| Candidate | License | Verdict |
| --- | --- | --- |
| `libclamav` linked in-process (`libclamav-py` 0.2.0) | **GPL-2.0** | **Rejected.** Linking `libclamav` makes MAFHUM a derivative work; the project's own guidance is that all software using `libclamav` must be GPL-compliant. Incompatible with the licensing posture. |
| `clamd` over its socket protocol (no Python dependency) | ClamAV GPL-2.0, **no linkage** | **Selected design.** Speaking to `clamd` across a socket does not link `libclamav`, so the GPL does not reach MAFHUM. The protocol is a short line protocol (`zINSTREAM`) over a Unix or TCP socket — implementable with the standard library alone. |
| `clamav-client` 0.7.2 (artefactual-labs) | GPL-2.0 | Viable but adds a GPL dependency where the socket protocol needs none. Released 2026-03-05; supports `clamd` and `clamscan`, TCP and Unix sockets. |
| `python-clamd` 0.0.2.dev0 | LGPL-2.1 | Viable; LGPL is weaker copyleft and used only as a client. Released 2025-11-25, Python 3.9–3.13. Preferred over the next entry. |
| `clamd` 1.0.2 | LGPL-3.0 | **Rejected.** Last published roughly 11 years ago; unmaintained. |
| YARA rules | BSD-3 | **Rejected as a substitute.** Pattern matching is not antivirus; shipping it would overstate coverage. Useful later as a complement. |
| Commercial/cloud scanning APIs | proprietary | **Rejected.** Violates the zero-mandatory-paid-service constraint. |

ClamAV itself is actively maintained by Cisco Talos (1.4.x LTS and 1.5.x current).

### Historical blocker (2026-10-23) — resolved on CI

The original assessment ran in a sandbox with no ClamAV (`command -v clamd
clamscan clamdscan freshclam yara yarac` returned absent for all) and no way to
provision a signature database. **That limitation applied to the development
sandbox only**, not to the product. It was resolved on GitHub-hosted runners:
staging Gate A installs a real engine and signatures, and
`.github/workflows/production-malware-acceptance.yml` builds
`deploy/clamd/Dockerfile`, waits for a SCAN-based healthcheck, verifies EICAR at
engine level and drives clean / EICAR / engine-outage uploads through the API.

### Implementation plan — status

1. `clamd` + `freshclam` as a separate service — **DONE** (`deploy/clamd/`, a
   named signature volume and a 12-hourly refresh loop).
2. `ClamdScanner` over `zINSTREAM` in bounded chunks — **DONE**
   (`services/documents/clamd.py`, TCP and Unix socket).
3. Fail closed on `ERROR`/`NOT_SCANNED` — **DONE** (`requires_clean()`), now also
   reported by `/health/ready` as `malware_scanner`.
4. Scan before the blob becomes readable — **DONE**. Quarantine in a separate
   store is **NOT** implemented: MAFHUM rejects and removes the upload at the
   boundary instead. A false positive is recoverable only by re-uploading.
5. Bounded scan time and size — **DONE** (`clamd_timeout_seconds`,
   `StreamMaxLength`, `max_upload_mb`).
6. Real-engine EICAR acceptance — **DONE** (Gate A and the production malware
   acceptance workflow). `malware_scanning_available` is derived from a live
   `PING`, never from a configuration flag.

Production now refuses to boot with `MALWARE_SCAN_REQUIRED=false` or with no
`clamd` address (`Settings.assert_production_ready()`), and
`tools/acceptance/prod_compose_contract.py` asserts that the Compose template
hard-codes the flag and gates the API on a healthy `clamd`. **Not verified on a
real owner host:** signature freshness, long-running daemon stability, and
recovery after a ClamAV mirror outage.

---

**Phase 7 update (2026-10-27):** the gap is closed. A `web` service and
`apps/web/Dockerfile` were added, and `.github/workflows/deployment-rehearsal.yml`
built and started the full db+api+web stack on a Docker-enabled runner, exercised
it over real HTTP, and performed a quiesced backup/restore drill. Run
**`40530008866`** concluded **success** (all 10 steps). This means
`docker compose up` has now been executed and observed — on CI infrastructure,
not in the development sandbox and not as a production service. The historical
assessment below is retained. See `docs/DEPLOYMENT_REHEARSAL_REPORT.md`.

## 2. Docker and reproducible deployment — FULL STACK RUN ON CI

**Status update, 2026-10-27 (after PR #1 merged into `main`).** Gate B of the
staging acceptance workflow (run `40377229156`, 2026-10-26) built
`apps/api/Dockerfile` on a GitHub-hosted runner and verified a non-root runtime,
`/health/ready`, readiness against PostgreSQL and graceful shutdown. The
assessment below is the **historical point-in-time record** from 2026-10-23, when
no container runtime was available in the development sandbox; its commands and
"no Dockerfile" observation describe that earlier tree, not the final one.

**Verified on CI since 2026-10-27:** `docker compose up` on the root
`docker-compose.yml` starts db + api + web, a real register → upload → process →
download journey runs over HTTP, and a quiesced backup is restored into a scratch
database with checksum verification (run `40530008866`). `apps/web/Dockerfile`
exists and is built in that run; the staging gates A/B/C also re-ran green on
`main` in run `40523074856`.

**Still not verified, and therefore still not claimed:** secret-injection
inspection of the built images (`docker history` / runtime `env`), a persistent
production deployment with managed PostgreSQL, TLS and private object storage,
and any long-running uptime or load evidence. `PRODUCTION-READY` is not the
outcome; the gates are acceptance evidence only.

Historical measurement (2026-10-23, superseded by Gate B):

```
$ command -v docker          # no output — binary absent
$ docker info                # docker: command not found
$ command -v podman nerdctl  # none
$ ls Dockerfile* docker-compose* compose* apps/*/Dockerfile*
# no Dockerfile or compose file tracked
```

At that time there was no container runtime in the sandbox and no Dockerfile in
the tree being assessed. Both conditions have since changed: the merged tree
tracks `apps/api/Dockerfile` and a root `docker-compose.yml`, and Gate B built
the API image on a runner that does have Docker. Keep the checklist below as the
list of checks still owed on the full stack.

### Commands still required once Docker exists

```bash
# 1. Images build from the actual Dockerfiles
docker build -t mafhum-api  -f apps/api/Dockerfile  apps/api
docker build -t mafhum-web  -f apps/web/Dockerfile  apps/web

# 2. No secret is baked into an image layer
docker history --no-trunc mafhum-api | grep -iE "session_secret|password|token" && echo "LEAK"
docker run --rm --entrypoint sh mafhum-api -c 'env | grep -iE "secret|password"' || echo "clean"

# 3. Stack comes up, waits for PostgreSQL, migrates, reports healthy
docker compose up -d --wait
docker compose ps
curl -fsS localhost:8000/health/ready

# 4. Migrations are applied by the container, not by hand
docker compose logs api | grep -i "alembic\|upgrade"

# 5. Storage and database survive a restart
docker compose restart api && curl -fsS localhost:8000/health/ready

# 6. Graceful shutdown drains the worker
docker compose stop api && docker compose logs api | tail -20

# 7. End-to-end journey inside the stack: upload → process → download → delete
python tools/e2e/live_e2e.py --base-url http://localhost:8000
```

Checks 2, 4 and 7 (and the `docker compose up` journey in checks 3–6) have not
been performed on the full stack. Check 6's graceful shutdown and the health
endpoint were exercised by Gate B on the single API container. Note that check
2's second `docker build` refers to `apps/web/Dockerfile`, which does not exist
yet — web deployment is an open Phase 7 item.

---

## 3. Dependency audit results

### Python — `pip-audit`

The first run **failed**, which is worth recording because it is not how CI
behaves:

```
Name       Version ID              Fix Versions
setuptools 66.1.1  PYSEC-2025-49   78.1.1
setuptools 66.1.1  PYSEC-2026-1918 70.0.0
setuptools 66.1.1  PYSEC-2026-3447 83.0.0
```

`setuptools` 66.1.1 is **Debian venv scaffolding** — `pip show setuptools`
reports `Required-by:` (nothing). It is not a MAFHUM dependency, but it did
contradict the project's own `pyproject.toml`, which declares
`requires = ["setuptools>=68"]`. CI passes because `setup-python` provides a
newer setuptools, so the local and CI results genuinely differed.

Upgrading to `84.2.1` satisfies the declared build requirement and clears all
three advisories:

```
$ pip-audit
No known vulnerabilities found
Name       Skip Reason
mafhum-api Dependency not found on PyPI and could not be audited: mafhum-api (0.3.0)
```

The `mafhum-api` skip is expected: it is the local editable install.

### Node — `npm audit`

```
$ npm audit --omit=dev
found 0 vulnerabilities            # exit 0
$ npm audit
5 high severity vulnerabilities    # all trace to `braces`
$ npm ls braces --omit=dev
`-- (empty)                        # not in the production tree
```

**Re-audit 2026-10-27 (Phase 6):** `npm audit` on the full `apps/web` tree now
reports **0 vulnerabilities** (exit 0), while `npm ls braces --all` still shows
`braces@3.0.3` via `eslint-config-next`. The earlier 5-high result below is a
historical measurement; the registry's current advisory data no longer flags it.
The triage is kept because nothing in the dependency path changed. A new
`apps/slides` audit gate was added for the PptxGenJS comparison package: after
pinning `image-size@2.0.4` via an override, it reports 0 vulnerabilities too.

`braces` (GHSA-vfj7-8cjw-p6xm, stack exhaustion via deeply nested patterns)
affects `<=3.0.3` and has **no patched release**. It reaches the tree only
through `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch`,
i.e. lint tooling that never ships.

`npm audit fix --force` is **rejected**: the only "fix" it offers is a
semver-major downgrade of `eslint-config-next` from 16.x to `14.2.35`, which
would break the lint configuration for Next 16 to silence a dev-only advisory.
**Residual risk: accepted and documented.** This is not a zero-vulnerability
claim — five advisories remain open in the dev tree.

---

## 4. Scaling and process-model limitations

These are properties of the current design, not bugs:

- **Quotas and the login rate limit are now database-enforced and safe across
  processes.** This entry previously warned that they were process-local and would
  admit roughly N times the intended quota under N workers. That was measured and
  found to be wrong, and the warning was removed rather than left to mislead:
  quotas are enforced in a transaction with `SELECT ... FOR UPDATE`
  (`_enforce_quota`), and the login throttle moved to a `login_attempts` table with
  an HMAC-derived `client_key` so no raw client identifier is stored.
  `tests/test_multi_instance_state.py` (9 tests) runs two independent application
  instances against one database and asserts exactly 5 uploads are admitted against
  a limit of 5 — not ~10 — that the login limit blocks exactly 6 attempts rather
  than 6 per replica, and that pruning expires old rows.
- **The document processing worker pool is still per-process.** That one is a real
  remaining constraint: each process runs its own bounded worker, so throughput
  scales with replicas but job claims must stay contention-safe. Do not assume the
  worker is shared.
- **No horizontal scaling.** The bounded in-process worker (ADR 0004) claims jobs
  with `FOR UPDATE SKIP LOCKED`, which is multi-process safe for *job claiming*,
  but the surrounding limits are not.
- **No encryption at rest.** Blob confidentiality relies on filesystem
  permissions (`0700`/`0600`) and a private root.
- **MAFHUM performs no backups.** The database and `STORAGE_ROOT` must be
  captured **together**; a database restore against a stale blob directory
  resurrects rows whose bytes no longer exist.
- **No OCR.** Scanned PDFs are detected and reported, not read.

---

## 5. What this document does not claim

A passing test suite does not prove the absence of vulnerabilities. The suite
demonstrates that the specified behaviours hold — ownership, concurrency,
configuration refusal, disclosure — and says nothing about unspecified ones. No
penetration test, fuzzing campaign or formal review has been performed on this
codebase.
