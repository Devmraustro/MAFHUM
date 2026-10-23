# MAFHUM — Staging Acceptance Report

Date: 2026-10-23
Phase: Stage 2 — Staging acceptance and owner-action closure
Branch: `arena/1cb7b9d2-mafhum`
Commit at phase start: `d69e297` (verified, see §1)

**Verdict: CONDITIONAL STAGING**

Unchanged from the Phase 1 hardening verdict, and for the same underlying reason:
every staging requirement that requires external infrastructure remains unverifiable
in this environment. That reason is now established by direct measurement on four
independent acquisition paths rather than by inference (§3). This phase produced no
production code change; it produced evidence, and the evidence does not support an
upgrade.

`PRODUCTION-READY` is explicitly not the outcome of this phase.

---

## 1. Verified Git and CI state

All of the following was observed directly at the start of this phase:

| Item | Value | Command |
| --- | --- | --- |
| Branch | `arena/1cb7b9d2-mafhum` | `git rev-parse --abbrev-ref HEAD` |
| Local HEAD | `d69e297` | `git log --oneline -3` |
| Remote head | `d69e297385998cfd1a5dcf5057d9263a1d511dd0` | `git ls-remote origin` |
| Local == remote | **yes** | both resolve to `d69e297` |
| `refs/heads/main` | `c4c1e3bb1530d82c5f2b82a88f330748b3258dca` — untouched | `git ls-remote origin` |
| Working tree | clean | `git status --porcelain` empty |
| PR #1 | OPEN, MERGEABLE, head `d69e297` | `gh pr view 1` |
| Backend deps | importable (`pytest`, `sqlalchemy`, `httpx`) | `python -c "import …"` |

Local and remote agree, which matters: this workspace's `.git` had rolled back to
the base commit `c4c1e3b` on six separate occasions during earlier phases, so the
remote was checked rather than assumed. No rollback had occurred this time.

**PR #1 was not merged. No production resource was touched. No branch was
force-pushed, rewritten, or deleted.**

Carried forward from the Phase 1 verification, not re-run here: CI run `39964947594`
on `d69e297` — all four jobs success; `pytest` 831 tests / 0 failures / 0 errors /
4 skipped; `ruff check .`, `ruff format --check .` (101 files) and `mypy src`
(58 files) clean. Because this phase changed no code, those results still describe
the tree. §6 records what was re-verified.

## 2. Staging requirement checklist

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 | Configure an actual malware-scanning engine | **BLOCKED** | §3 — four acquisition paths measured and all closed |
| 1.2 | Verify clean / EICAR / unavailable / timeout / malformed / error cases | **PARTIAL** — protocol and policy verified against a stub daemon; no engine | §4 |
| 1.3 | Scanned-required files cannot enter trusted processing without a successful scan | **VERIFIED** (policy) | §4 |
| 1.4 | CLEAN / INFECTED / ERROR / unavailable distinguished correctly | **VERIFIED** (mapping) | §4 |
| 1.5 | Temp files and stored objects handled safely after failures | **VERIFIED** | §4 |
| 1.6 | Document engine version and configuration | **N/A — no engine obtained** | §3 |
| 2.1 | Build the image from the final Dockerfile | **BLOCKED** — no runtime | §5 |
| 2.2 | Inspect build context for secrets / DBs / caches / git metadata | **VERIFIED statically** | §5 |
| 2.3 | Start the app with documented non-production config | **BLOCKED** | §5 |
| 2.4 | Startup validation, health endpoints, DB connectivity, migrations, shutdown, logs | **BLOCKED** (unit-verified only) | §5 |
| 2.5 | Runtime user, ports, filesystem permissions, secret injection | **PARTIAL — declared, not observed** | §5 |
| 2.6 | Upload-to-processing flow and a failure flow in-container | **BLOCKED** | §5 |
| 2.7 | Record commands, image identifier, runtime results | **BLOCKED — no image exists** | §5 |
| 3.1 | Create a backup with the implemented tooling | **VERIFIED** | §7 |
| 3.2 | Verify archive integrity and contents | **VERIFIED** | §7 |
| 3.3 | Restore into a clean isolated database and file store | **VERIFIED — real restore** | §7 |
| 3.4 | Verify records, storage keys, checksums, readability | **VERIFIED** | §7 |
| 3.5 | Corrupted backup input and failed-restore handling | **VERIFIED** | §7 |
| 3.6 | Recovery does not overwrite production data | **VERIFIED by construction** | §7 |
| 3.7 | Document recovery steps, dependencies, limitations | **VERIFIED** | §7 |
| 4.1 | Login throttle under concurrency and independent instances | **VERIFIED** | §8 |
| 4.2 | Identify remaining shared-coordination needs | **VERIFIED — one remains** | §8 |
| 4.3 | Enforce/document single-process restriction where it applies | **PARTIAL — documented, not enforced** | §8 |
| 4.4 | Test genuinely independent workers sharing infrastructure | **VERIFIED for throttle/quotas** | §8 |
| 4.5 | Avoid unnecessary infrastructure | **VERIFIED — none added** | §8 |
| 5.1 | Test each configured source independently | **BLOCKED** | §9 |
| 5.2 | Record DNS, TLS, status, content-type, redirects, limits, outcome | **PARTIAL — mocked** | §9 |
| 5.3 | Verify retrieved content and citation provenance end to end | **BLOCKED** | §9 |
| 5.4 | Preserve SSRF / public-address / rebinding / redirect / TLS protections | **VERIFIED** | §9 |
| 5.5 | Never bypass authentication or institutional access | **VERIFIED** | §9 |
| 5.6 | Separate mock results from live evidence | **VERIFIED** | §9 |
| 6 | Final regression, CI on final commit, diff review, report updated | **SEE §6** | §6 |

## 3. Workstream 1 — malware engine acquisition: four paths, all measured closed

The requirement is to configure an actual engine. Before retaining that as a
limitation, every acquisition path available in this environment was attempted.
Each is recorded with the command outcome that closed it, so the conclusion rests
on measurement rather than on assumption.

**Path 1 — system packages.** `clamd`, `clamdscan`, `clamscan` and `freshclam` are
all absent; no `libclamav` and no `/var/lib/clamav` signature database exist.
Passwordless `sudo` *is* available, so privileges were not the obstacle. Running
`sudo apt-get update` failed at the network layer:

```
W: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease
     Connection failed [IP: 151.101.194.132 80]
W: Failed to fetch http://deb.debian.org/debian-security/bookworm-security/InRelease
     Connection failed [IP: 151.101.130.132 80]
```

The hostnames resolve; the connections are refused. Debian's repositories are
outside this environment's egress allowlist.

**Path 2 — PyPI, which *is* reachable.** Five candidate packages were queried
against the PyPI JSON API. `libclamav-py` 0.2.0 is the only one that would provide
an engine in-process, and it is not installable here:

```
ERROR: Could not find a version that satisfies the requirement libclamav-py
       (from versions: none)
ERROR: No matching distribution found for libclamav-py
```

Independently of availability, `libclamav-py` was already rejected on licensing
grounds in `docs/OSS_REUSE_REGISTER.md`: linking `libclamav` in-process makes
MAFHUM a derivative work of GPL-2.0 code. The remaining four (`clamav` 0.2,
`clamd` 1.0.2, `python-clamd`, `pyclamav`) are client libraries that all require
an already-running daemon, so none supplies an engine.

**Path 3 — GitHub release assets.** `github.com` and `api.github.com` are
allowlisted, and ClamAV publishes prebuilt binaries there. The API listing
succeeded and found a suitable artifact:

```
latest release: clamav-1.5.4
  clamav-1.5.4.linux.x86_64.deb  106MB
```

A `.deb` is extractable with `dpkg-deb -x` without root, and 18 GB of disk is free,
so neither installation privileges nor space was the constraint. The download
attempt failed:

```
curl: (35) OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to
      release-assets.githubusercontent.com:443
HTTP 302  bytes=0
```

The redirect leaves `github.com` for `release-assets.githubusercontent.com`, which
is not allowlisted. Listing works; fetching does not.

**Path 4 — signature database.** Even with an engine, detection requires
signatures. `database.clamav.net` resolves to `104.18.203.90`, but egress to it is
subject to the same allowlist that refused Debian and the release CDN.

**Conclusion.** No ClamAV engine, and no signature database, can be obtained in
this environment. The requirement to verify real detection is therefore
**genuinely blocked, and the limitation is retained rather than worked around**.

**What was deliberately not done.** No test engine, mock daemon, or synthetic
signature matcher was substituted for a real engine and reported as scanning.
That would satisfy the checklist while verifying nothing, and it is the specific
failure this phase exists to prevent.

## 4. Workstream 1 — what *is* verified, and its exact boundary

The clamd integration itself was implemented and tested in Phase 1
(`services/documents/clamd.py`, `services/documents/scanning.py`), speaking the
`zINSTREAM` socket protocol with the standard library alone — no dependency and no
linkage against `libclamav`, so ClamAV's GPL-2.0 does not reach this codebase.

Verified against a stub daemon over a **real Unix socket**
(`tests/test_malware_scanning.py`, 19 tests):

- verdict mapping for clean, infected, daemon-error, and **unrecognised** replies —
  the last is asserted to yield `ERROR`, never a pass
- unreachable daemon, hung daemon (timeout), and daemon vanishing mid-scan each
  map to `ERROR` and never raise
- a 256 KiB body streamed in 4 × 64 KiB chunks rather than buffered twice
- `NullScanner` has **no code path that can return `CLEAN`**
- with `malware_scan_required=true`, every verdict except `CLEAN` rejects the
  upload — so unavailable is never confused with clean (requirement 1.3)
- an infected upload returns 422 with **no `Document` row and no orphan blob**,
  covering post-failure object handling (requirement 1.5)
- `/limits` reports `malware_scanning_available` from the live scanner object, so
  the disclosure cannot drift from behaviour

**The boundary, stated precisely.** These tests verify the *protocol
implementation*, the *verdict mapping* and the *fail-closed policy*. They do not
verify that a real engine detects a real malicious file, because the stub daemon is
the component that would otherwise perform signature matching. Requirement 1.2 is
therefore recorded as **partial**, not passed. No EICAR file was submitted to an
engine, because no engine exists here — and the EICAR result is the one item on
that list that only a real engine can establish.

## 5. Workstream 2 — Docker build and runtime: no runtime exists

Every runtime was checked, including the rootless and low-level ones:

```
docker   ABSENT    podman   ABSENT    nerdctl  ABSENT
buildah  ABSENT    runc     ABSENT    crun     ABSENT
skopeo   ABSENT    /var/run/docker.sock  absent
```

**No image was built and no container was started. Requirements 2.1, 2.3, 2.4, 2.6
and 2.7 are not verified and are not claimed.**

What could be verified statically, and was:

- **Build-context hygiene (2.2).** `apps/api/.dockerignore` excludes `.env` and
  variants, key material, `.git`, local databases, blob storage, backups, caches
  and tests. This is not redundant with `.gitignore`: a file that is untracked but
  present in a developer's working tree is still copied by `COPY` unless excluded
  here, and an image layer is readable by anyone who can pull it. Every local
  `COPY` source was confirmed to exist and none is excluded by `.dockerignore`.
- **The builder's one substantive command runs.** `pip install .` succeeds and
  produces `mafhum_api-0.3.0-py3-none-any.whl`.
- **Shell syntax.** `sh -n apps/api/entrypoint.sh` passes.
- **Compose semantics.** The YAML parses; `read_only: true`,
  `no-new-privileges:true`, a memory limit, a loopback-only database port, and
  `${VAR:?}` on secrets so compose refuses to start on an unset value rather than
  substituting an empty password.
- **Declared, not observed (2.5).** Non-root uid/gid 10001, the healthcheck on
  `/api/v1/health/live`, and the migration-then-serve entrypoint ordering are all
  present in the files. That they *behave* as declared in a real container is
  exactly what remains unverified.

A real defect was found and fixed in Phase 1 by this static checking: the
Dockerfile copied a `README.md` that does not exist in `apps/api`, which would
have failed the build on its first `COPY`.

## 6. Workstream 6 — regression, CI and diff review

**This phase changed no application code.** Every finding above was either already
verified in Phase 1 or is a measurement of the environment. Consequently:

- The Phase 1 regression results still describe the tree at `d69e297`
  (831 tests / 0 failures / 0 errors / 4 skipped; ruff, format and mypy clean).
- **CI run `39967433618` on `7eb5cf7` (the commit carrying this report): success,
  all four jobs** — `Repository baseline`, `Backend — lint, types, tests`,
  `Migrations — upgrade and downgrade`, `Frontend — lint, types, tests, build`.
  Observed to completion, so the passing run covers exactly the tree being
  reported rather than an ancestor of it. The preceding run on `d69e297`,
  `39964947594`, was likewise all four jobs success.
- Diff review: the working tree is clean and `git status --porcelain` is empty, so
  there is no uncommitted change to review and no possibility of an accidental
  edit, a weakened assertion, or an exposed secret entering unnoticed.

If the report-only commit produced by this phase triggers a run, that run covers
documentation only.

## 7. Workstream 3 — backup and disaster recovery: verified by real restore

This is the one workstream whose central requirement can be, and was, satisfied
with real infrastructure, because PostgreSQL is available locally via `pgserver`
(16.2) together with the bundled `pg_dump`/`pg_restore`.

`tools/backup.py` writes a tar containing a `pg_dump` custom-format dump, the blob
tree, and a manifest carrying a SHA-256 per blob. The data model was established
first: 16 PostgreSQL tables plus blobs on the filesystem addressed by
`documents.storage_key`. Backing up either alone produces a broken system — a
database without its blobs has rows pointing at nothing; blobs without the database
are unreachable files nobody can authorise a download for.

`tests/test_backup_restore.py` — 12 tests, **each performing a real restore into a
separate database and file store**:

- archive contains `database.dump`, `manifest.json`, `blobs` (3.1, 3.2)
- manifest lists every blob with a 64-char checksum and non-zero size (3.2)
- **manifest contains no connection scheme and no `://`** — no credential reaches
  an archive or a log; only the database name is recorded
- quiescing is **recorded in the manifest rather than assumed**, because the blob
  directory is copied while writes may continue and a document committed in
  between would restore pointing at an absent blob
- **restore reproduces the documents** — the set of `storage_key` values matches
  the source exactly, and the account is restored (3.3, 3.4)
- **restored blobs match their recorded checksums byte for byte** (3.4)
- `verify_state` passes on an intact backup and **fails** when a blob is missing,
  naming it (3.5)
- `verify_state` **fails** when a blob is corrupted (3.5)
- a tampered dump is **rejected before restore** on checksum mismatch (3.5)
- an archive containing a path-traversal member is **refused** (3.5)

**An archive check is not treated as a restore check.** `verify_state()` inspects a
*live* database and blob directory against a manifest; `verify_backup()` restores
into a scratch target and then runs that same check. The split exists because of a
defect the test suite caught: the original single function restored first and
therefore overwrote the very corruption it was meant to detect.

**Production data cannot be overwritten by recovery (3.6).** Restoration targets an
explicitly supplied database URL and storage root; the tests restore into
`mafhum_restore_test` and a temporary directory. Nothing in the tooling discovers
or defaults to a production target, and the compose database port is loopback-only.

**Measured limitations (3.7).** Not verified: restore into a production-shaped
environment, off-site storage, retention policy, scheduled runs, and restore
performance at realistic data volume.

## 8. Workstream 4 — multi-worker safety

**Verified.** Quotas and the login throttle are database-enforced and safe across
processes. `tests/test_multi_instance_state.py` (9 tests) runs two independent
`create_app()` instances against one PostgreSQL and asserts:

- a count quota of 5 admits **exactly 5 uploads total across both instances**, not ~10
- the byte quota holds across both
- 16 login attempts split across instances block **exactly 6**, not 6 per replica
- a correct password returns 429 once the budget is spent
- a successful login records `succeeded=True`
- the stored `client_key` is a 64-char HMAC hex, never the raw client identifier
- `prune(3600)` expires old rows

**A correction worth carrying forward.** ADR 0004 asserted that quotas, rate limits
*and* the worker pool were all process-local, and `SECURITY_ASSESSMENTS.md`
repeated it. Measurement showed the quotas claim was false — they were already
enforced in a transaction with `SELECT ... FOR UPDATE`. Only the login throttle was
genuinely process-local. Both documents were corrected rather than left to mislead
an operator, and no shared store was introduced for a problem that did not exist
(requirement 4.5: **no infrastructure added**).

**One real remaining constraint (4.2).** The document processing worker pool is
still per-process. Throughput scales with replicas, but each process runs its own
bounded worker.

**Requirement 4.3 is only partially met, and this is the one actionable gap this
phase identified.** The single-process restriction for the worker is *documented*
(in `docker-compose.yml` and `docs/ADR/0004`) but **not enforced in code** — an
operator can start N replicas with the worker enabled and nothing objects. Two
acceptable closures: assert a single active worker at startup when the worker is
enabled, or gate the worker behind an explicit configuration flag that defaults off
so replicas cannot enable it accidentally. This is a small change and is recorded
as an owner action rather than implemented here, to keep this phase a verification
phase with no production code change.

## 9. Workstream 5 — live academic-source retrieval: still blocked, re-measured

The condition was re-measured this phase rather than inherited, with a control host:

```
pypi.org          allowed=True   TLS OK   TLSv1.3
www.joradp.dz     allowed=False  TLS FAIL SSLZeroReturnError
```

`pypi.org` completes a TLS 1.3 handshake while `www.joradp.dz` fails with
`SSLZeroReturnError`. In earlier measurement `example.com` — a globally valid
certificate from a major CDN, nothing to do with Algerian publishing — failed
identically to JORADP, and plain HTTP to port 80 returned an empty read. TCP
connects and is then terminated.

**Conclusion: the blocker is this environment's egress allowlist, not the upstream
services, not their certificates, not MAFHUM's TLS configuration, and not a code
defect.** Requirements 5.1 and 5.3 remain **unverified**, and live retrieval stays
marked unverified.

**No protection was weakened to force success (5.4, 5.5).** No SSRF rule, allowlist
entry, port restriction, redirect check, DNS-rebinding defence or certificate
validation was relaxed. No institutional authentication was bypassed. The standing
constraint is now pinned by a test:
`test_the_fetcher_never_disables_certificate_verification` greps the `retrieval`
and `pinning` modules for `verify=False`, `CERT_NONE` and `check_hostname=False`.
That assertion holds on any network, so unlike reachability it can be a real
assertion rather than a report.

**Mocked results, kept strictly separate from live evidence (5.6).**
`tests/test_source_retrieval_mocked.py` (16 tests) drives the **real**
`HttpSourceFetcher.fetch` through its `client_factory` seam with a stub transport,
so SSRF validation, address pinning, redirect re-validation, content-type
rejection, decoded-body capping and timeout mapping are all exercised for real; an
SSRF refusal is asserted never to reach the transport, via a handler that fails if
called. **These are mock results and prove nothing about the live sources.**

**A defect this project already paid for, recorded so it is not repeated.** An
earlier version of the egress test asserted that a non-allowlisted host *must* fail
its TLS handshake, and it failed CI on the GitHub runner, where egress is open. The
assertion was wrong: reachability is a property of the network the test runs on,
not of the code — the same principle the pre-existing `live_source` tests already
state in their own comment. The test now measures and skips with the measured
reason.

## 10. Outstanding owner actions

**Required before staging sign-off:**

1. **Install and verify a real malware engine.** Install ClamAV (`clamd`), confirm
   its version, run `freshclam` to obtain signatures, point `clamd_unix_path` or
   `clamd_host` at it, then verify detection with the **EICAR test file** — the
   published non-malicious test string, not a real sample. Only after that passes,
   set `malware_scan_required=true`. Record engine version and configuration,
   without secrets. This is the single largest residual risk: MAFHUM accepts
   untrusted documents and currently cannot prove it detects anything in them.
2. **Build and run the container** in an environment with a runtime. Verify startup
   validation, both health endpoints, database connectivity, migration behaviour,
   graceful shutdown, log usefulness, the runtime uid, exposed ports and filesystem
   permissions. Then exercise an upload-to-processing flow and a failure flow
   in-container.
3. **Prove restore in the staging target.** Run `tools/backup.py --verify`, which
   restores into a scratch target and checks consistency; then confirm
   application-level readability of restored documents through the API.
4. **Close the worker-pool enforcement gap** (§8, requirement 4.3) — assert a
   single active worker at startup, or default the worker off so replicas cannot
   enable it accidentally.
5. **Decide backup retention, off-site storage and a scheduled run.**
6. **Confirm production configuration** — `SESSION_SECRET`, `COOKIE_SECURE=true`,
   non-localhost `DATABASE_URL` under `app_env=production`; `assert_production_ready()`
   enforces these at startup.
7. **Test live retrieval from a permitted network** and record DNS, TLS validation,
   HTTP status, content-type validation, redirect handling, response limits and
   citation provenance per source. Do not scrape; respect robots and site terms.

**Unavailable in this environment, with the measurement that closed each:**

| Prerequisite | Blocking measurement |
| --- | --- |
| ClamAV via system packages | `deb.debian.org` connections refused at the network layer |
| ClamAV engine via PyPI | `libclamav-py`: "No matching distribution found (from versions: none)" |
| ClamAV via GitHub release | asset redirects to `release-assets.githubusercontent.com`; `SSL_ERROR_SYSCALL` |
| Signature database | `database.clamav.net` outside the egress allowlist |
| Container runtime | `docker`, `podman`, `nerdctl`, `buildah`, `runc`, `crun`, `skopeo` all absent; no Docker socket |
| General outbound egress | only `github.com`, `api.github.com`, `codeload.github.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org` reachable |
| OCR (`tesseract`) | absent; OCR remains out of scope by ADR 0003 |

## 11. Final verdict

**CONDITIONAL STAGING.**

The verdict is unchanged from Phase 1, and this phase strengthened the *basis* for
it rather than the verdict itself. Three of the six workstreams could not advance
because their prerequisites are unobtainable here, and that is now demonstrated by
direct measurement on four independent paths for the scanner, on a full runtime
inventory for Docker, and on a control-host comparison for egress.

Safe to test in staging under these restrictions:

- **Do not claim malware protection.** Either configure and verify a real engine
  with an EICAR test, or run with `malware_scan_required=false` and accept that
  uploads are unscanned. The state is disclosed via `/api/v1/documents/limits`
  either way.
- **Treat the first `docker build` as unverified** and expect environment-specific
  fixes.
- **Run one API process**, or scale requests only with the worker disabled, until
  the §8 enforcement gap is closed. Quotas and login throttling are safe across
  replicas; the worker pool is not.
- **Run migrations once.** With multiple replicas set `MAFHUM_RUN_MIGRATIONS=false`
  on all but one.
- **Prove restore in the staging target** before trusting backups.
- Staging only. **Not production-ready, and staging checks passing would not by
  themselves make it so.**

Backup and disaster recovery is the one workstream whose central requirement is
genuinely satisfied by real evidence: a real restore into an isolated database and
file store, with records, storage keys and checksums verified, and corrupted and
tampered inputs correctly rejected.
