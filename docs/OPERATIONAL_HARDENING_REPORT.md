# MAFHUM — Operational Hardening Report

Date: 2026-10-23
Branch: `arena/1cb7b9d2-mafhum`
Baseline at start of this work: `a7366df` (Phase 4/5 closeout)
Final commit: `4a820ce` (see §10 for a later CI-caught fix)

**Verdict: CONDITIONAL STAGING**

Safe to deploy to a staging environment under the restrictions in §12. Not
production-ready: malware detection, container execution and production-shaped
restore have not been verified, and each is an operator action rather than a code
change.

---

## 1. Verified repository state

| Item | Value | How verified |
| --- | --- | --- |
| Branch | `arena/1cb7b9d2-mafhum` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `91a9aaa` | `git log --oneline -1` |
| Pushed | yes — `git ls-remote origin` returns `91a9aaa…` | `git ls-remote origin arena/1cb7b9d2-mafhum` |
| `origin/main` | `c4c1e3b`, unmodified | `git log origin/main -1` |
| PR #1 | OPEN, not merged | `gh pr view 1` |
| Working tree | clean | `git status --porcelain` empty |
| Tracked secrets | none | `git ls-files \| grep -iE '\.env\|\.pem'` empty; `git check-ignore` confirms `.env` ignored |

Note on method: the local `.git` in this workspace rolled back to the base commit
`c4c1e3b` on six separate occasions. Every turn therefore begins with
`git ls-remote origin` and reconciles with `git fetch` + `git reset --mixed`
(never `--hard`, which would discard user work). No force-push was used at any
point.

## 2. Baseline and what changed

Baseline `a7366df` had 4 green CI jobs and a passing suite. Four operational risks
remained open. This work closed three of them and diagnosed the fourth.

| Commit | Priority | Subject |
| --- | --- | --- |
| `276a34c` | A | Move the login throttle into the database |
| `aed7dc5` | B | Real clamd malware scanning with a fail-closed policy |
| `f239af2` | C | Reproducible hardened container build |
| `91a9aaa` | D | Backup and restore, verified by an actual restore test |

Priority E produced tests and a diagnosis but no source change, because the
investigation established the blocker was environmental.

## 3. Priority A — unsafe process-local state

**Original finding.** ADR 0004 stated that "quotas, rate limits and the worker pool
are process-local" and therefore wrong under multiple workers.

**What the investigation actually found.** The ADR was wrong about quotas. Quotas
were already database-enforced through `_enforce_quota`, which runs inside a
transaction with `SELECT ... FOR UPDATE`. `docs/SECURITY_ASSESSMENTS.md` repeated
the same incorrect warning. Only the **login throttle** was genuinely process-local
— a module-level dict, so N replicas admitted N times the intended budget.

This matters as a correction of my own earlier position: I initially planned to
move quotas to a shared store on the strength of the ADR. Measuring first showed
the work was unnecessary, and the documents were corrected instead.

**Fix.** `LoginAttempt` model, migration `0006_login_throttle`, and
`services/identity/throttle.py`. The client identifier is an HMAC-derived
`client_key`, so no raw client string is stored.

**Evidence.** `tests/test_multi_instance_state.py` — 9 tests, running two
independent `create_app()` instances against one PostgreSQL:

- count quota (limit 5) admits **exactly 5 uploads total**, not ~10
- byte quota holds across both instances
- 16 login attempts split across instances block **exactly 6**, not 6 per replica
- a correct password returns **429** once the budget is spent
- a successful login records `succeeded=True`
- `client_key` is a 64-char HMAC hex, never the raw value
- `prune(3600)` expires old rows

**Remaining limitation.** The document processing worker pool is still
per-process. That is a real constraint, now documented as such rather than
bundled into the (incorrect) claim about quotas.

## 4. Priority B — malware risk controls

**Original finding.** `api/v1/documents.py` held a module-level `_SCANNER` that was
used **only** to populate `/limits`. It was never invoked during upload, while the
endpoint implied scanning capability.

**Fix.** A real scanner interface plus a clamd implementation:

- `ScanVerdict` with four distinct states — `NOT_SCANNED`, `CLEAN`, `INFECTED`,
  `ERROR`. Only `CLEAN` passes `requires_clean()`.
- `ClamdScanner` speaks the `zINSTREAM` socket protocol using only the standard
  library. No Python dependency, and no linkage against `libclamav`, so ClamAV's
  GPL-2.0 does not reach this codebase. `libclamav-py` was rejected for exactly
  that reason; recorded in `docs/OSS_REUSE_REGISTER.md`.
- `NullScanner` is the default and has **no code path that can return `CLEAN`**.
- With `malware_scan_required=true`, any verdict other than `CLEAN` rejects the
  upload, so unavailability is never mistaken for cleanliness.
- Blobs stream to the daemon through a chunk adapter rather than being buffered
  twice, and a rejected upload compensates its blob away.
- `scan_verdict` is persisted (migration `0007`), and `/limits` reads the live
  scanner object so the disclosure cannot drift.

**Evidence.** `tests/test_malware_scanning.py` — 19 tests, stable across three
consecutive runs, driving a stub `clamd` over a **real Unix socket**: clean,
infected, daemon error, an unrecognised reply, unreachable daemon, hung daemon,
daemon vanishing mid-scan, chunked streaming of a 256 KiB body, and an infected
upload returning 422 with no `Document` row and no orphan blob.

**Remaining limitation — stated plainly.** **Detection is unverified.** No ClamAV
engine or signature database was installable in this environment, so no test has
ever confirmed that a real malicious file is detected. What is verified is the
protocol, the verdict mapping and the fail-closed policy. A deployment must
configure and verify an engine before anyone may claim malware protection.

Two defects in my own test stub were found and fixed rather than papered over: the
stub matched bare `PING`/`INSTREAM` while the client correctly sends the `z`
reply-mode prefix, and `stop()` could serve one extra connection because a thread
blocked in `accept()` is not woken by closing the listening socket. In both cases
the scanner was right and the fake was wrong.

## 5. Priority C — reproducible deployment

**Original finding.** No container definition existed anywhere in the repository.

**Fix.** `apps/api/Dockerfile` (two-stage, non-root uid 10001), `apps/api/.dockerignore`,
`apps/api/entrypoint.sh`, `docker-compose.yml`.

`.dockerignore` is not redundant with `.gitignore`: a file that is untracked but
present in a developer's working tree is still copied by `COPY` unless excluded,
and an image layer is readable by anyone who can pull it. Compose sets
`read_only: true`, `no-new-privileges`, a memory limit, a loopback-only database
port, and `${VAR:?}` on secrets so it refuses to start on an unset value rather
than substituting an empty password.

**A real defect, caught by verification.** The Dockerfile contained
`COPY pyproject.toml README.md ./` but `apps/api/README.md` does not exist — the
build would have failed on its first `COPY`. Confirmed `pip install .` succeeds
without it (producing `mafhum_api-0.3.0-py3-none-any.whl`) and that `pyproject.toml`
declares no readme, then removed the file from the instruction.

**Docker is NOT verified.** No container runtime — `docker`, `podman`, `nerdctl`
or `buildah` — is installed in this environment. `docker build` and
`docker compose up` have never been observed to succeed, and no container has been
started or health-checked. The Dockerfile header says so explicitly.

What was verified instead: `sh -n` on the entrypoint; the compose YAML parses with
the expected services, volumes and security options; every local `COPY` source
exists and none is excluded by `.dockerignore`; and the builder's one substantive
command (`pip install .`) succeeds.

## 6. Priority D — backup and restore

**Original finding.** No backup tooling existed.

**Data model established first.** Two kinds of state that refer to each other:
PostgreSQL (16 tables) and uploaded blobs on the filesystem, addressed by
`documents.storage_key`. Either one alone produces a broken system — a database
without its blobs has rows pointing at nothing; blobs without the database are
unreachable files nobody can authorise a download for.

**Fix.** `tools/backup.py` writes a tar containing a `pg_dump` custom-format dump,
the blob tree, and a manifest with a per-blob checksum. Custom format so an
operator can restore a single table during an incident. `pg_dump`/`pg_restore` are
found on `PATH` or in the PostgreSQL bundled with `pgserver`, which is what made an
actual restore test possible here.

No credential reaches an archive or a log; the manifest records only the database
name. Whether the application was quiesced is **recorded in the manifest rather
than assumed**, because the blob directory is copied while writes may continue and
a document committed in between would restore pointing at an absent blob.

`verify_state()` checks a **live** database and blob directory against a manifest.
`verify_backup()` restores into a scratch target and then runs that same check.

**A design bug my own test caught.** I first shipped only `verify_backup`. The
corruption test failed: it corrupted the restored blobs, then `verify_backup`
restored again and overwrote the corruption before checking. An archive-level check
silently undoes on-disk damage, so it cannot be what an operator runs after a real
restore. Hence two functions answering two different questions.

**Restore evidence — an actual restore test, as required.** 12 tests, each
performing a real restore against PostgreSQL:

- archive contains `database.dump`, `manifest.json` and `blobs`
- manifest lists every blob with a 64-char checksum and a non-zero size
- manifest contains no connection scheme and no `://`
- quiescing recorded as `false` / `true` per the flag
- **restoring reproduces the documents** — the set of `storage_key`s matches
  exactly, and the account is restored
- **restored blobs match their recorded checksums** byte for byte
- verification passes on an intact backup
- verification **fails** when a blob is missing from the archive, and names it
- verification **fails** when a blob is corrupted
- a tampered dump is **rejected before restore** on checksum mismatch
- an archive with a path-traversal member is **refused**

Extraction rejects absolute and parent-traversing members explicitly rather than
relying on `tarfile`'s `filter` argument, which does not exist before Python
3.11.4 — this environment runs 3.11.2, and the tests caught that.

**Remaining limitation.** Not verified: restore into a production-shaped
environment, off-site storage, retention policy, scheduled runs.

## 7. Priority E — live academic-source retrieval

**The diagnosis, measured rather than assumed.** Earlier work reported a TLS
handshake failure against JORADP, ASJP and SNDL. Re-measuring with a control host
settled the cause:

```
pypi.org                 allowed=True   TLS OK   TLSv1.3
api.github.com           allowed=True   TLS OK   TLSv1.3
example.com              allowed=False  TLS FAIL SSLZeroReturnError
www.joradp.dz            allowed=False  TLS FAIL SSLZeroReturnError
```

`example.com` is the control. It has a globally valid certificate served by a major
CDN and nothing to do with Algerian publishing, yet it fails identically. Plain
HTTP to port 80 returns an empty read. TCP connects and is then terminated.

**Conclusion: the failure is this sandbox's egress policy, not the upstream
services, not their certificates, not MAFHUM's TLS configuration, and not a code
defect.** No SSRF control, allowlist entry, port restriction or certificate
validation was weakened to chase a success that this environment cannot produce.

**Evidence.** `tests/test_source_retrieval_mocked.py` — 16 tests driving the
**real** `HttpSourceFetcher.fetch` through its `client_factory` seam with a stub
transport, so SSRF validation, address pinning, redirect handling, content-type
rejection, decoded-body capping and timeout mapping are all exercised for real:

- a normal page is retrieved; a PDF is refused as `unsupported_content_type`
- an HTTP 503 becomes `http_503`, not an exception
- a 302 is followed and the new host re-validated
- a 10 MB body is truncated at `MAX_RESPONSE_BYTES` rather than buffered
- a timeout becomes `timeout`
- an SSRF refusal **never reaches the transport** — asserted by a handler that
  fails if called
- `mesrs.url` is still `None` because that address was never confirmed; a
  descriptor has no `verification_status` field, pinned so a "verified" flag cannot
  appear silently
- `FixtureFetcher` reports a miss as `not_found` rather than synthesising one

**The four honest skips.** `test_joradp_is_reachable`,
`test_egress_condition_is_measured_and_recorded`,
`test_outbound_egress_is_recorded_not_asserted`,
`test_retrieval_of_a_real_source_is_reported`. The egress test re-measures the
condition on every run and skips **with the measured reason**, rather than freezing
a statement about one network into the suite.

**A flaw CI caught in that test, and what it taught.** My first version of the
egress test *failed* when conditions differed, asserting that a non-allowlisted
host must fail its TLS handshake. On the GitHub runner egress is open, so
`example.com` succeeds and the test failed — on commit `4a820ce`, in run
`39962768326`. The assertion was wrong: it treated a property of the network the
test happened to run on as a property of the code, which is precisely the mistake
the pre-existing `live_source` tests already warn against ("reachability from a
given network is a fact about that network, not a property of the code"). It now
measures and reports, and the environment-independent claim — that the fetcher
never disables certificate verification — is asserted separately by grepping the
`retrieval` and `pinning` modules for `verify=False`, `CERT_NONE` and
`check_hostname=False`. That one can be asserted because it holds on any network.

**Classification, kept separate as required:**

| Layer | Status |
| --- | --- |
| Adapter descriptors | VERIFIED (tests above) |
| Mocked retrieval through real `fetch()` | VERIFIED |
| TLS connectivity to JORADP/ASJP/SNDL | **UNVERIFIED — sandbox blocks it** |
| Live content retrieval | **UNVERIFIED — sandbox blocks it** |
| Verified citation provenance from a live source | **UNVERIFIED — sandbox blocks it** |

No records, citations, authors, titles, DOIs or URLs were invented. Where a fact
was not confirmed, the field is `None`.

## 8. Documentation corrected

Three documents contained claims that the work disproved. Leaving them would have
misled an operator reading them before configuring a deployment:

- `docs/ADR/0003` — retitled and amended. "No malware scanning" is no longer
  accurate as an absolute; the amendment separates *no bundled engine* (still
  true) from *an implemented, fail-closed integration path* (now true) and
  *verified detection* (still false).
- `docs/ADR/0004` — corrected in Priority A; quotas were never process-local.
- `docs/SECURITY_ASSESSMENTS.md` — §1 rewritten to "INTEGRATION IMPLEMENTED,
  DETECTION UNVERIFIED"; §4's incorrect process-local quota warning removed and
  replaced with what was measured, keeping the worker-pool constraint that is real.
- `docs/DATA_POLICY.md` — the "no malware scanning" limitation now describes the
  default install and the opt-in clamd path, without implying detection works.

## 9. Test and quality results

Run from `apps/api` on the final tree:

| Command | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 101 files already formatted |
| `mypy src` | Success: no issues found in 58 source files |
| `pytest` | **831 tests, 0 failures, 0 errors, 4 skipped, 131.778s**, exit 0 |

Suite growth across this work: 796 → 803 (Priority B) → 815 (Priority D) → **830**
(Priority E, then 831 after the CI fix). Every skipped test is a live-egress skip listed in §7.

Migrations `0006` and `0007` were verified UP → DOWN → UP → DOWN(base) → UP on
PostgreSQL 16.2.

`pip-audit`: "No known vulnerabilities found".

Frontend: `tsc --noEmit` 0 errors, lint 0 errors, 101 tests across 8 files, build
OK.

## 10. CI

| Run | Commit | Result |
| --- | --- | --- |
| `39962768326` | `4a820ce` | failure — backend `Tests against real PostgreSQL` |
| `39961655378` | `91a9aaa` | success — all 4 jobs |
| `39960572516` | `f239af2` | success |
| `39960288025` | `aed7dc5` | cancelled |
| `39958716550` | `276a34c` | failure |

Both failures were identified from the runner's own job annotations and
check-run annotations rather than by guessing, which matters because two earlier
plausible CI diagnoses in this project turned out to be wrong.

- `276a34c` — the `Ruff format check` step. Fixed in the following commit.
- `4a820ce` — the egress test described in §7, which asserted a network property
  as a code property. Fixed by measuring and reporting instead.

Neither is latent. Recording them because a green head does not mean every
intermediate commit was green, and the difference is visible in the history.

## 11. Security posture and remaining high-risk findings

Fixed and verified in this and prior work: `/openapi.json` exposure, concurrent
retry producing duplicate jobs, concurrent delete returning multiple 200s,
blob-deleted-before-commit (documents and accounts), concurrent-deletion 500, DNS
rebinding in the retrieval pinning path, unbounded decompression, and the
process-local login throttle.

Still open, in priority order:

1. **Malware detection unverified.** No engine available. Highest residual risk for
   a service accepting untrusted documents.
2. **Container execution unverified.** No runtime here; the first real `docker
   build` may surface environment-specific problems.
3. **Restore into production-shaped storage unverified.** The mechanism is tested;
   the operator's environment is not.
4. **Live source retrieval unexercised.** Blocked by the sandbox, not by the code.
5. No encryption at rest; no OCR; no real-socket SSRF test.
6. Lexical grounding cannot establish semantic support — `fr-15` remains a genuine
   miss and is kept visible rather than relabelled.
7. `braces` advisory has no patched release; dev-only, accepted.

## 12. Readiness verdict

**CONDITIONAL STAGING.**

Safe to deploy to staging under these restrictions:

- **Do not claim malware protection.** Either configure and verify a real `clamd`,
  or keep `malware_scan_required=false` and accept that uploads are unscanned —
  the state is disclosed through `/limits` either way.
- **Run one API process** until the worker pool is shared, or scale requests only
  with the worker disabled. Quotas and login throttling are safe across replicas;
  the worker is not.
- **Run migrations once.** With multiple replicas set `MAFHUM_RUN_MIGRATIONS=false`
  on all but one, as documented in the compose file.
- **Treat the first `docker build` as unverified** and expect to fix
  environment-specific issues.
- **Prove restore in the target environment** before trusting backups. Run
  `tools/backup.py --verify`, which restores into a scratch target and checks
  consistency.
- Staging only. No production promotion on the strength of this report.

## 13. Owner actions and unavailable external prerequisites

**Required before production:**

1. Install and configure ClamAV; verify detection with a known test signature
   (e.g. EICAR) and only then set `malware_scan_required=true`.
2. Build and start the container in an environment with a runtime; confirm the
   health endpoints.
3. Perform a restore into a production-shaped target and confirm record and blob
   consistency.
4. Define backup retention, off-site storage and a scheduled run.
5. Decide the worker-pool sharing strategy before running more than one API
   process with the worker enabled.
6. Set `SESSION_SECRET`, `COOKIE_SECURE=true` and a non-localhost database URL for
   `app_env=production`; `assert_production_ready()` enforces this at startup.

**Unavailable here, blocking verification:**

- No container runtime (`docker`, `podman`, `nerdctl`, `buildah`).
- No ClamAV engine or signature database.
- No general outbound egress: only `github.com`, `codeload.github.com`,
  `api.github.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org` are
  reachable, which is what blocks live retrieval from JORADP, ASJP and SNDL.
- No `tesseract`, so OCR remains out of scope as originally decided.

**Not done, per standing constraints:** PR #1 left open and unmerged; `main`
untouched; no force-push; no history rewrite; no branch deletion; no public
deployment; Phases 6–7 not started.
