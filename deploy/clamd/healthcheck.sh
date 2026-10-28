#!/bin/sh
# Healthcheck for the clamd container.
#
# A TCP connect / PING is NOT enough: clamd can accept a connection while it is
# still loading signatures, and a daemon with an empty database can answer PING
# yet refuse every scan. A real SCAN of a known-clean file that returns exit 0
# is the smallest proof that the engine is loaded and able to give a verdict.
#
# clamdscan exit codes: 0 = OK (clean), 1 = FOUND, 2 = error/unreachable.
probe="/tmp/mafhum-clamd-probe.$$"
printf 'MAFHUM clamd healthcheck probe\n' > "$probe"
# `timeout` matters: clamd can accept a TCP connection while it is still loading
# signatures, and a bare clamdscan would then wait forever for a SCAN reply.
timeout 10 clamdscan --config-file=/etc/clamav/clamdscan.conf --no-summary "$probe" >/dev/null 2>&1
rc=$?
rm -f "$probe"
[ "$rc" -eq 0 ]
