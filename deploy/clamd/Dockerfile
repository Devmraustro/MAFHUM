# Real ClamAV daemon for the MAFHUM production stack.
#
# Why our own image instead of `clamav/clamav`: the upstream image generates its
# configuration in an /init entrypoint and can start a bundled freshclam daemon.
# MAFHUM needs an explicit, auditable TCP-only configuration, a healthcheck that
# proves a real SCAN works (not just that a socket accepts connections), and a
# first-boot signature bootstrap that fails loudly. This small image keeps all
# three in the repository where a reviewer can read them.
#
# The Debian package is ClamAV proper (the same engine Gate A installs via apt),
# not a Python binding: libclamav is GPL-2.0 and speaking to a separate daemon
# over a socket keeps that licence out of MAFHUM's own code.
FROM debian:12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clamav-daemon \
        clamav-freshclam \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY clamd.conf /etc/clamav/clamd.conf
COPY clamdscan.conf /etc/clamav/clamdscan.conf
COPY entrypoint.sh /usr/local/bin/mafhum-clamd-entrypoint.sh
COPY healthcheck.sh /usr/local/bin/mafhum-clamd-healthcheck.sh

RUN chmod 0755 /usr/local/bin/mafhum-clamd-entrypoint.sh \
        /usr/local/bin/mafhum-clamd-healthcheck.sh \
    && mkdir -p /var/lib/clamav /run/clamav /var/log/clamav \
    && chown -R clamav:clamav /var/lib/clamav /run/clamav /var/log/clamav

# Signature databases are the only mutable state. Keeping them on a named
# volume means a restart does not re-download ~200 MB from ClamAV's mirrors.
VOLUME ["/var/lib/clamav"]
EXPOSE 3310

USER clamav

HEALTHCHECK --interval=15s --timeout=20s --start-period=900s --retries=40 \
    CMD ["/usr/local/bin/mafhum-clamd-healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/mafhum-clamd-entrypoint.sh"]
