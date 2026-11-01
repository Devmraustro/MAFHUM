# External ClamAV scanner — deployment runbook (Phase 2)

This is the runbook for the **single hard production blocker**: Vercel
Functions cannot host a resident clamd daemon, so `MALWARE_SCAN_REQUIRED=true`
production deployments need an **external scanner host** reachable from the
Functions. The fail-closed policy is already implemented and tested — with no
reachable scanner, uploads are refused (never marked clean) — so the worst
case of a mis-provisioned scanner is an upload outage, never a security gap.

**Boundary:** this document *prepares* the deployment; it creates no
infrastructure, spends nothing, and has **not been executed against a real
host** (no Docker engine exists in the development environment — see
`docs/OPERATIONAL_VERIFICATION_REPORT.md` §2). Every configuration block below
must be validated during provisioning; record the evidence listed in §7.

## 1. Architecture decision (evaluated against the master criteria)

| Criterion | **A. clamd + TLS tunnel on a small VPS (recommended)** | B. clamd + private network | C. HTTPS scanning microservice |
|---|---|---|---|
| Reachability from Vercel | ✅ public TCP with TLS | ⚠️ needs Vercel Secure Compute (Enterprise) | ✅ public HTTPS |
| TLS + service auth | ✅ stunnel TLS + optional client cert (mTLS) | ✅ network-private, no TLS needed | ✅ native HTTPS + bearer |
| Upload size / streaming | ✅ clamd INSTREAM streams; limits set in `clamd.conf` | ✅ same | ⚠️ body limits of the wrapper |
| Timeouts / resource limits | ✅ existing `CLAMD_TIMEOUT_SECONDS` + daemon limits | ✅ same | ⚠️ two timeout budgets to align |
| Availability / failure | ✅ simple host, `restart: unless-stopped`; MAFHUM fails closed on outage | ✅ same | ⚠️ one more moving part |
| Cost / complexity | ✅ smallest VPS + one container pair | ❌ Enterprise networking feature | ❌ new service to build + operate |
| Security / isolation | ✅ mTLS; no inbound MAFHUM data at rest on the scanner | ✅ strongest network isolation | ⚠️ scanner API is another attack surface |
| **Application code changes** | **none** — MAFHUM already speaks clamd INSTREAM over TCP (`CLAMD_HOST`/`CLAMD_PORT`) | none | **new client code required** (not built — the owner has not chosen this) |

**Selected: A.** It requires zero changes to MAFHUM, reuses the audited
`deploy/clamd` image, and every failure path is already tested (stub-socket
tests + CI's real-engine acceptance + the DAST outage case). Option C remains
viable later; its client code should only be written after the owner selects it.
Option D — "run clamd on Vercel" — is impossible (resident process, mutable
signature volume) and is not considered further.

## 2. Secure networking requirements (Option A)

Clamd's INSTREAM protocol is **plaintext TCP with no TLS of its own**; the
traffic carries unscanned document bytes, so it must not cross the public
internet unencrypted. The wrapper pattern:

```
Vercel Function ──TLS:44300──▶ stunnel ──plaintext:127.0.0.1:3310──▶ clamd
```

1. **TLS termination with stunnel** in front of clamd; the MAFHUM client stays
   unchanged and connects to `CLAMD_HOST:44300`.
2. **Mutual TLS (recommended):** require a client certificate so port
   discovery + scanning are denied to strangers. Generate a private CA, one
   server cert (scanner host) and one client cert (stored as a Vercel
   environment secret only if/when MAFHUM gains TLS-client support — until
   then, see step 3 for the compensating control).
3. **Compensating controls while the client cannot do mTLS** (the current
   client is plaintext-after-tunnel): a long random stunnel port (≠ common
   ports), `verifyPeer` requiring a *server* cert chain only, fail2ban on the
   stunnel port, and connection limits (` MaxConnectionQueueLength 16`,
   `MaxThreads 8` already in `clamd.conf`). IP allowlisting of Vercel's egress
   ranges alone is **not** sufficient (egress IPs are shared and region-dynamic)
   — treat it as an additional filter, never the only one.
4. **Firewall:** inbound to the stunnel port only; nothing else exposed; clamd
   listens on loopback only (`TCPAddr 127.0.0.1` override on the host — the
   tracked `deploy/clamd/clamd.conf` binds `0.0.0.0` because CI's compose
   network needs it; on a public VPS bind it to loopback and let stunnel dial
   in).
5. **No persistence of scanned content:** clamd keeps nothing; the host needs
   no document storage. Log only verdicts (the tracked config logs to a local
   file with `LogFileMaxSize 0` rotation-off — keep it small or ship to the
   host's journal).

## 3. Reference deployment configuration (validate during provisioning)

Run on the scanner VPS (Docker Compose v2), reusing the audited image built
from `deploy/clamd/` — **build it on the VPS from the same commit as the
deployment** (`docker build -t mafhum-clamd:<commit-short> deploy/clamd`) and
record the digest; do not use `:latest`:

```yaml
# scanner compose (owner-hosted; not tracked in this repository)
name: mafhum-scanner
services:
  clamd:
    image: mafhum-clamd:<commit-short>
    restart: unless-stopped
    volumes:
      - clamd_db:/var/lib/clamav
    tmpfs:
      - /tmp
    security_opt: ["no-new-privileges:true"]
    healthcheck:                      # real SCAN, not a socket probe
      test: ["CMD-SHELL", "/usr/local/bin/mafhum-clamd-healthcheck.sh"]
      interval: 15s
      timeout: 20s
      retries: 40
      start_period: 900s
  stunnel:
    image: <pinned-by-digest stunnel image>   # operator verifies + pins
    restart: unless-stopped
    depends_on: [clamd]
    ports:
      - "44300:44300"
    volumes:
      - ./stunnel.conf:/etc/stunnel/stunnel.conf:ro
      - ./certs:/etc/stunnel/certs:ro
    security_opt: ["no-new-privileges:true"]
volumes:
  clamd_db:
```

with `stunnel.conf` (paths per your cert layout):

```ini
[clamd]
accept  = 0.0.0.0:44300
connect = 127.0.0.1:3310
cert    = /etc/stunnel/certs/scanner.crt
key     = /etc/stunnel/certs/scanner.key
; requireClient = yes        # enable when MAFHUM's client supports mTLS
```

and, **on the VPS**, override the tracked config's bind address so clamd is
not publicly reachable (stunnel is the only listener):

```bash
# mount this as /etc/clamav/clamd-override or edit before build:
#   TCPAddr 127.0.0.1        (instead of 0.0.0.0)
```

Signature freshness: the image's entrypoint runs `freshclam` at boot and every
12 h, and its healthcheck performs a real SCAN — a daemon with a dead signature
set does not report healthy. Verify `daily.cvd` mtime after boot and once a day
thereafter.

## 4. Environment variables (MAFHUM side — owner sets on Vercel)

| Variable | Value for this architecture |
|---|---|
| `CLAMD_HOST` | the scanner host's DNS name |
| `CLAMD_PORT` | `44300` (the stunnel TLS port) |
| `CLAMD_TIMEOUT_SECONDS` | `10.0` default is fine; raise only if cross-continent RTT + scan time approaches it |
| `CLAMD_UNIX_PATH` | leave **empty** (TCP mode) |
| `MALWARE_SCAN_REQUIRED` | `true` — mandatory in production; `assert_production_ready` refuses `false` |

Stream-size parity check (already satisfied by the tracked `clamd.conf`):
`StreamMaxLength 64M` and `MaxFileSize 64M` exceed the application's
`MAX_UPLOAD_MB` default (20 MB). If the owner raises `MAX_UPLOAD_MB`, raise
those two scanner limits to match, or large uploads will be cut off by the
daemon rather than the application.

## 5. Health-check strategy

* **Scanner-local:** the tracked `deploy/clamd/healthcheck.sh` performs a real
  bounded `clamdscan` of a clean probe — a container is `healthy` only when
  scanning works, never merely when the socket is open.
* **Application-side:** MAFHUM's scan client probes with a real `zPING\0`
  before declaring the engine available (`clamd.py::available`); with scanning
  required and the engine down, `/health/ready` reports `degraded` +
  `malware_scanner=unavailable` and uploads fail closed (proven by the local
  DAST outage case).
* **Monitoring:** alert on `mafhum_malware_scanner_available == 0` while
  `mafhum_malware_scan_required == 1` (`MafhumMalwareScannerUnavailable`, the
  tracked critical rule) and on the `up{job="mafhum"}` scrape. The scanner host
  itself can also run the uptime-monitor pattern against its stunnel port
  (TCP reachability) — a PING is not SCAN-READY, so treat it as a
  network-level check only.

## 6. Acceptance commands (run these against the REAL host; record everything)

From any machine with the Vercel deployment URL (and, for 1–2, network access
to the scanner):

```bash
# 1. TLS + protocol sanity (plaintext INSTREAM is the client's job; this only
#    proves the tunnel speaks):
openssl s_client -connect scanner.example.com:44300 -brief </dev/null

# 2. Engine + signature evidence on the scanner host:
docker exec mafhum-scanner-clamd-1 clamdscan --version   # engine version
docker exec mafhum-scanner-clamd-1 sh -c 'ls -l --time-style=full-iso /var/lib/clamav'

# 3. Three-case acceptance through MAFHUM's own API (the authoritative gate):
#    a. clean DOCX  -> 201/200 accepted
#    b. EICAR DOCX  -> 422, details.reason == "malware_detected"
#       (build it with: python apps/api/tools/eicar_probe.py eicar.docx)
#    c. scanner down -> 422/503 with a scan_* reason; NEVER clean, NEVER
#       malware_detected  (docker stop mafhum-scanner-clamd-1, retry b with a
#       clean file, docker start ...)
#    Record: HTTP status, reason code, request_id, timestamp, for each case.
```

The repository's CI gate (`.github/workflows/production-malware-acceptance.yml`)
runs the same three cases against a real engine on disposable runners; §6 here
is the same evidence collected **through the real deployed path**.

## 7. Evidence to record before production traffic

1. Build digest of `mafhum-clamd:<commit>` on the scanner host.
2. Container-start → first `healthy` (SCAN-READY) duration, cold volume.
3. `clamscan --version` engine string + `daily.cvd` timestamp.
4. The three-case acceptance results with request IDs.
5. One deliberate outage: scanner stopped → API refuses uploads with a
   `scan_*` reason and `/health/ready` degrades; scanner restarted → uploads
   recover without MAFHUM redeployment.
6. TLS certificate expiry date (calendar the renewal).

## 8. Rollback instructions

* **Scanner-side rollback is transparent to MAFHUM:** redeploy the previous
  image/digest or restore the previous signature volume on the scanner host;
  MAFHUM keeps `CLAMD_*` unchanged and simply resumes receiving CLEAN/FOUND
  verdicts. No application redeploy, no migration.
* **While the scanner is down, the system is already in its safe state:**
  uploads fail closed. Do **not** "roll back" by setting
  `MALWARE_SCAN_REQUIRED=false` — production configuration refuses it
  (`config.py` asserts it), and disabling scanning to restore upload
  availability is exactly the failure mode this architecture exists to prevent.
* **Schema/data rollback:** not applicable — the scanner holds no MAFHUM data.
  Follow `docs/ROLLBACK_GUIDE.md` for application-side rollback, which is
  independent of the scanner.
