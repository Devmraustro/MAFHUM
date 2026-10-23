# Security assessments

Point-in-time assessments of security capabilities MAFHUM does **not** have, and
of the deployment tooling that could not be exercised. Every result below was
produced by running a command in the development sandbox; nothing here is
inferred from documentation.

Sandbox: Debian 12, Python 3.11.2, Node v22.22.3, PostgreSQL 16.2 (via
`pgserver`), 2 CPUs, 3939 MB RAM.

---

## 1. Malware scanning — INTEGRATION IMPLEMENTED, DETECTION UNVERIFIED

**No document has ever been virus-checked by this system, and no test has ever
confirmed that a real malicious file is detected.** That statement is the load-bearing
one and it remains true.

What changed on 2026-10-23: the code can now scan. `services/documents/clamd.py`
speaks the clamd `zINSTREAM` socket protocol, so an operator who points
`clamd_unix_path` or `clamd_host` at a running `clamd` gets real scanning, with the
verdict persisted on the document and `malware_scan_required` able to fail the
upload closed. A default install still configures no engine and still scans nothing.

The distinction to hold on to: **the fail-closed policy and the protocol are
verified; detection is not.** No ClamAV engine or signature database was installable
in this environment, so "this deployment detects malware" is a claim only the
operator who configures and verifies an engine may make.

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

### Blocker

No scanner could be installed or exercised here. Verified absent:

```
$ command -v clamd clamscan clamdscan freshclam yara yarac
clamd: absent   clamscan: absent   clamdscan: absent
freshclam: absent   yara: absent   yarac: absent
```

A signature database (`freshclam`, on the order of hundreds of MB) also cannot
be provisioned, and the zero-budget constraint rules out a paid service.
**End-to-end malware detection is therefore UNVERIFIED.** The socket client is
not written either, because a client that cannot be tested against a real engine
would only create the impression of scanning.

### Implementation plan

1. Provision `clamd` plus `freshclam` as a **separate container/service**, not a
   Python dependency. Signature updates are an operational task with its own
   schedule and monitoring.
2. Implement `ClamdScanner(MalwareScanner)` using `zINSTREAM` over a Unix socket,
   streaming in bounded chunks so a large upload is never buffered whole.
3. **Fail closed.** Unreachable daemon, timeout or protocol error → `ERROR`.
   Callers that require scanning must reject `ERROR` and `NOT_SCANNED` exactly as
   they reject `INFECTED` (`requires_clean()` already encodes this).
4. Scan **before** the blob becomes readable, and quarantine rather than delete
   on `INFECTED` so a false positive is recoverable.
5. Bound scan time and size; a slow engine must not become a denial of service.
6. Only then flip `malware_scanning_available`, and add tests that run against a
   real `clamd` with the EICAR test file as a known-positive fixture.

Until step 6 passes against a real engine, no MAFHUM documentation, UI string or
API response may describe an upload as scanned, clean or safe.

---

## 2. Docker and reproducible deployment — NOT VERIFIED

**No image was built and no Compose stack was run.** Claiming otherwise would be
false.

Verified blockers:

```
$ command -v docker          # no output — binary absent
$ docker info                # docker: command not found
$ command -v podman nerdctl  # none
$ ls Dockerfile* docker-compose* compose* apps/*/Dockerfile*
# no Dockerfile or compose file tracked
```

There is no container runtime **and** no Dockerfile in the repository, so there
is nothing to build yet. A sibling branch (`arena/dee89266-mafhum`) contains a
root `docker-compose.yml`; it was not adopted here because it was never verified
against this tree.

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

Checks 2, 4, 6 and 7 are the ones most likely to reveal a real defect; none has
been performed.

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
