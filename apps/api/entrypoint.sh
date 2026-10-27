#!/bin/sh
# MAFHUM API container entrypoint.
#
# Applies database migrations explicitly, then hands off to the server. Migrations
# are not run at import time: a schema that fails to migrate must stop the
# container rather than let it serve traffic against a schema the code does not
# match.
#
# NOT YET VERIFIED BY EXECUTION: no container runtime was available where this was
# written, so this script has not been run inside a built image. The migration
# command it invokes is the same one verified directly against PostgreSQL 16.2
# (see docs/OPERATIONAL_HARDENING_REPORT.md); what is unverified is the container
# wiring around it.
set -e

# --- validate configuration before doing any work ---------------------------
# assert_production_ready() refuses to boot with development defaults when
# APP_ENV=production: an unset SESSION_SECRET, COOKIE_SECURE=false, or a database
# URL pointing at localhost. Failing here gives a clear message instead of a
# container that starts and then misbehaves.
echo "[entrypoint] validating configuration"
python -c "
from mafhum_api.config import get_settings
settings = get_settings()
settings.assert_production_ready()
print('[entrypoint] configuration accepted (app_env=%s)' % settings.app_env)
"

# --- apply migrations -------------------------------------------------------
# A single worker applies them; see the note in docker-compose.yml about running
# more than one replica.
if [ "${MAFHUM_RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying database migrations"
    alembic upgrade head
    echo "[entrypoint] migrations applied"
else
    echo "[entrypoint] MAFHUM_RUN_MIGRATIONS=false; skipping migrations"
fi

# --- start the server -------------------------------------------------------
# exec replaces this shell with the server so it receives SIGTERM directly and can
# drain in-flight requests, rather than being orphaned and killed after a grace
# period.
echo "[entrypoint] starting: $*"
exec "$@"
