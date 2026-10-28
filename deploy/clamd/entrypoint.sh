#!/bin/sh
# Bootstrap a real signature set, keep it fresh, then run clamd in foreground.
#
# clamd refuses to start (or answers every scan with an error) when no core
# signature database is present. Treating that as "healthy" would turn the
# fail-closed switch into a permanent upload outage, so bootstrap is explicit
# and any failure aborts the container.
set -eu

has_signatures() {
    find /var/lib/clamav -maxdepth 1 -type f \
        \( -name '*.cvd' -o -name '*.cld' -o -name '*.hdb' \) 2>/dev/null \
        | grep -q .
}

if ! has_signatures; then
    echo "[clamd] no signature database on the volume; running freshclam" >&2
    freshclam --no-warnings || {
        echo "[clamd] FATAL: freshclam could not download a signature set" >&2
        exit 1
    }
else
    echo "[clamd] reusing the signature database on the volume" >&2
fi

if ! has_signatures; then
    echo "[clamd] FATAL: still no signature database after freshclam" >&2
    exit 1
fi

# Signature updates in the background. Two checks a day keeps the set within
# ClamAV's normal freshness window without a second container or a host cron.
# The daemon reloads its database itself; a failed refresh is logged and the
# next attempt still happens. This is deliberately a loop in the entrypoint
# rather than a packaged service unit systemd would have to supervise.
(
    while true; do
        sleep 43200
        freshclam --no-warnings >&2 || echo "[clamd] signature refresh failed" >&2
    done
) &

echo "[clamd] starting daemon on TCP 3310" >&2
exec clamd --config-file=/etc/clamav/clamd.conf --foreground=true
