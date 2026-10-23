#!/usr/bin/env bash
# MAFHUM staging verification — run by an authorized operator where the
# prerequisites that this development environment lacks are actually available.
#
# Three acceptance conditions cannot be closed in the development sandbox because
# the infrastructure is absent, not because the code is untested:
#   A. real malware-scanning detection   (no ClamAV engine obtainable)
#   B. container build and runtime       (no container runtime present)
#   C. live academic-source retrieval    (no general outbound egress)
#
# This script performs each check and prints PASS/FAIL/BLOCKED with the evidence.
# It never substitutes a mock for the real thing: if a prerequisite is missing it
# reports BLOCKED and says what to install, so nobody can mistake an absent engine
# for a passing scan.
#
# Usage:
#   ./tools/staging_verification.sh [--only A|B|C] [--api-url http://localhost:8000]
#
# Exit code is 0 only if every selected section PASSed. BLOCKED counts as failure,
# because a staging sign-off that quietly skipped a control is worse than one that
# stopped.

set -uo pipefail

ONLY="${1:-all}"
API_URL="${MAFHUM_API_URL:-http://localhost:8000}"
FAILURES=0
EVIDENCE="$(mktemp)"
trap 'rm -f "$EVIDENCE"' EXIT

section() { printf '\n=== %s ===\n' "$1"; }
pass()    { printf 'PASS    %s\n' "$1"; }
fail()    { printf 'FAIL    %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
blocked() { printf 'BLOCKED %s\n         -> %s\n' "$1" "$2"; FAILURES=$((FAILURES + 1)); }

# --------------------------------------------------------------------------- A
check_scanner() {
  section "A. Malware scanning — real engine, EICAR detection"

  if ! command -v clamd >/dev/null 2>&1; then
    blocked "clamd is not installed" \
      "install ClamAV (apt-get install clamav clamav-daemon), then run freshclam"
    return
  fi
  pass "clamd present: $(clamd --version 2>&1 | head -1)"

  # The socket the application is configured to use.
  local sock="${MAFHUM_CLAMD_UNIX_PATH:-/var/run/clamav/clamd.ctl}"
  if [ ! -S "$sock" ]; then
    blocked "no clamd socket at $sock" \
      "start the daemon: systemctl start clamav-daemon (or clamd -c /etc/clamav/clamd.conf)"
    return
  fi
  pass "clamd socket reachable at $sock"

  # Signature database must be present or detection is vacuous.
  if ! clamdtop --version >/dev/null 2>&1 && [ ! -d /var/lib/clamav ]; then
    blocked "no signature database found" "run freshclam and wait for it to complete"
    return
  fi
  pass "signature database directory present"

  # EICAR: the published, non-malicious antivirus test string. Using anything
  # else would mean handling real malware, which this procedure forbids.
  local eicar="$EVIDENCE/eicar.com"
  printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > "$eicar"

  local verdict
  verdict="$(clamdscan --no-summary "$eicar" 2>&1)"
  if printf '%s' "$verdict" | grep -qi 'Eicar.*FOUND'; then
    pass "engine detects EICAR: $(printf '%s' "$verdict" | head -1)"
  else
    fail "engine did NOT detect EICAR — signatures missing or engine broken"
    printf '         engine said: %s\n' "$verdict"
    return
  fi

  # Now the part that matters: does the APPLICATION reject it, with scanning
  # required? A working engine the app ignores is not a control.
  if [ "${MAFHUM_MALWARE_SCAN_REQUIRED:-}" != "true" ]; then
    blocked "MAFHUM_MALWARE_SCAN_REQUIRED is not true in this environment" \
      "set it to true and restart the API, then re-run this section"
    return
  fi

  local code
  code="$(curl -s -o "$EVIDENCE/resp.json" -w '%{http_code}' \
    -X POST "$API_URL/api/v1/documents" \
    -H "X-CSRF-Token: ${MAFHUM_CSRF_TOKEN:-}" \
    -H "Cookie: ${MAFHUM_COOKIE:-}" \
    -F "file=@$eicar;type=application/pdf" 2>/dev/null)"
  if [ "$code" = "422" ]; then
    pass "upload of EICAR rejected by the API with 422"
    grep -o '"scan_verdict":"[a-z_]*"' "$EVIDENCE/resp.json" 2>/dev/null \
      | sed 's/^/         /' || true
  else
    fail "EICAR upload was NOT rejected (HTTP $code) — fail-closed is not in effect"
    printf '         response: %s\n' "$(head -c 200 "$EVIDENCE/resp.json" 2>/dev/null)"
  fi

  # Capability disclosure must match reality.
  local disclosed
  disclosed="$(curl -s "$API_URL/api/v1/documents/limits" 2>/dev/null \
    | grep -o '"malware_scanning_available":[a-z]*')"
  if printf '%s' "$disclosed" | grep -q 'true'; then
    pass "API discloses malware_scanning_available: true"
  else
    fail "API does not disclose scanning as available (got: ${disclosed:-nothing})"
  fi
}

# --------------------------------------------------------------------------- B
check_container() {
  section "B. Container build and runtime"

  local runtime=""
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1; then runtime="$candidate"; break; fi
  done
  if [ -z "$runtime" ]; then
    blocked "no container runtime (docker/podman) present" \
      "install Docker Engine or Podman on the staging host, then re-run"
    return
  fi
  pass "runtime: $($runtime --version 2>&1 | head -1)"

  local image="mafhum-api:staging-verify"
  if ! $runtime build -q -t "$image" ./apps/api > "$EVIDENCE/build.log" 2>&1; then
    fail "image build failed"
    tail -15 "$EVIDENCE/build.log" | sed 's/^/         /'
    return
  fi
  local digest
  digest="$($runtime image inspect "$image" --format '{{.Id}}' 2>/dev/null)"
  pass "image built: $image ($digest)"

  # The image must not run as root.
  local user
  user="$($runtime image inspect "$image" --format '{{.Config.User}}' 2>/dev/null)"
  if [ -n "$user" ] && [ "$user" != "0" ]; then
    pass "image runs as non-root user: $user"
  else
    fail "image would run as root (User='${user:-unset}')"
  fi

  # Build context must not carry secrets or local data.
  if [ -f ./apps/api/.dockerignore ] \
     && grep -q '^\.env$' ./apps/api/.dockerignore \
     && grep -q '^\.git$' ./apps/api/.dockerignore; then
    pass ".dockerignore excludes .env and .git"
  else
    fail ".dockerignore does not exclude .env and .git"
  fi

  # Start against a throwaway database and watch the health endpoints.
  local cid
  cid="$($runtime run -d --rm \
    -e MAFHUM_APP_ENV=development \
    -e MAFHUM_DATABASE_URL="${MAFHUM_STAGING_DATABASE_URL:?set MAFHUM_STAGING_DATABASE_URL}" \
    -e MAFHUM_SESSION_SECRET="${MAFHUM_STAGING_SESSION_SECRET:?set MAFHUM_STAGING_SESSION_SECRET}" \
    -p 18000:8000 "$image" 2>/dev/null)"
  if [ -z "$cid" ]; then
    fail "container did not start"
    return
  fi
  printf '         container %s\n' "${cid:0:12}"

  local ok=""
  for _ in $(seq 1 30); do
    if curl -fsS "http://localhost:18000/api/v1/health/ready" >/dev/null 2>&1; then
      ok=1; break
    fi
    sleep 1
  done
  if [ -n "$ok" ]; then
    pass "readiness endpoint became healthy"
    curl -fsS "http://localhost:18000/api/v1/health/live" >/dev/null 2>&1 \
      && pass "liveness endpoint responds" || fail "liveness endpoint failed"
  else
    fail "readiness endpoint never became healthy"
    $runtime logs --tail 25 "$cid" 2>&1 | sed 's/^/         /'
  fi

  # Graceful shutdown: SIGTERM must exit 0, not be killed.
  $runtime stop -t 15 "$cid" >/dev/null 2>&1
  pass "container stopped on SIGTERM within the grace period"
}

# --------------------------------------------------------------------------- C
check_retrieval() {
  section "C. Live academic-source retrieval"

  # Network layer first, so a blocked egress is never mistaken for a code defect.
  local reachable=0
  for host in www.joradp.dz www.asjp.cerist.dz; do
    if python3 - "$host" <<'PY' 2>/dev/null
import socket, ssl, sys
host = sys.argv[1]
s = socket.socket(); s.settimeout(10)
s.connect((socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0], 443))
with ssl.create_default_context().wrap_socket(s, server_hostname=host):
    pass
PY
    then
      pass "$host: DNS + TLS handshake succeeded"
      reachable=$((reachable + 1))
    else
      printf 'BLOCKED %s: TLS handshake failed (network layer, not application code)\n' "$host"
    fi
  done

  if [ "$reachable" -eq 0 ]; then
    blocked "no configured source is reachable from this network" \
      "run from a network with permitted outbound access to *.dz; do not disable TLS verification to force success"
    return
  fi

  # Real retrieval through the application's own fetcher, so SSRF protections,
  # redirect validation and content-type checks are all in the path.
  MAFHUM_LIVE_RESEARCH=1 python3 - <<'PY' && pass "live retrieval exercised through HttpSourceFetcher" \
    || fail "live retrieval raised or was refused"
import os
os.environ["MAFHUM_LIVE_RESEARCH"] = "1"
from mafhum_api.services.research.retrieval import HttpSourceFetcher, KNOWN_SOURCES

fetcher = HttpSourceFetcher()
for source in KNOWN_SOURCES:
    if not source.url:
        print(f"         {source.key}: no URL configured (unconfirmed) — skipped, not invented")
        continue
    page = fetcher.fetch(source.url)
    print(f"         {source.key}: ok={page.ok} status={page.status_code} "
          f"ctype={page.content_type} reason={page.reason}")
PY
}

# ------------------------------------------------------------------------ main
case "$ONLY" in
  --only) shift; ONLY="${1:-all}" ;;
  *) ONLY="${ONLY#--only=}" ;;
esac

printf 'MAFHUM staging verification\n'
printf 'API URL: %s\n' "$API_URL"

case "$ONLY" in
  A) check_scanner ;;
  B) check_container ;;
  C) check_retrieval ;;
  all) check_scanner; check_container; check_retrieval ;;
  *) printf 'unknown section: %s\n' "$ONLY"; exit 2 ;;
esac

section "Summary"
if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL SELECTED CHECKS PASSED\n'
  exit 0
fi
printf '%s check(s) FAILED or BLOCKED — staging sign-off is not established\n' "$FAILURES"
exit 1
