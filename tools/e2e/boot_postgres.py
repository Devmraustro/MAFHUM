"""Boot a real PostgreSQL server (pgserver's bundled binaries) for live E2E runs.

Prints the SQLAlchemy URL on stdout and then idles, so the server outlives the
launching shell. Used only for local verification; never part of the product.
"""

import os
import tempfile
import time

os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.mkdtemp(prefix="mafhum-xdg-"))
os.chdir(os.environ.get("MAFHUM_REPO_ROOT", os.getcwd()))

import pgserver  # noqa: E402

DATA_DIR = os.environ.get("MAFHUM_E2E_PGDATA", ".e2e/pgdata")
os.makedirs(DATA_DIR, exist_ok=True)

server = pgserver.get_server(DATA_DIR, cleanup_mode=None)
uri = server.get_uri()

# pgserver returns a libpq URI over a unix socket; convert for SQLAlchemy.
import urllib.parse  # noqa: E402

parts = urllib.parse.urlsplit(uri)
query = urllib.parse.parse_qs(parts.query)
# pgserver puts the unix-socket directory in the `host` *query* parameter, not
# in the authority component, so it has to be read from there.
socket_dir = (query.get("host") or [""])[0]
dbname = parts.path.lstrip("/") or "postgres"
user = parts.username or "postgres"

sqlalchemy_url = (
    f"postgresql+psycopg://{user}@/{dbname}"
    f"?host={urllib.parse.quote(socket_dir, safe='')}"
)
assert socket_dir, f"no socket dir in {uri!r}"

out = os.environ.get("MAFHUM_E2E_URL_FILE", ".e2e/pg_url.txt")
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write(sqlalchemy_url)

print("PG_READY", sqlalchemy_url, flush=True)

while True:
    time.sleep(3600)
