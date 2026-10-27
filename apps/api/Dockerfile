# MAFHUM API container image.
#
# Build context is apps/api. Build from the repository root:
#   docker build -f apps/api/Dockerfile -t mafhum-api:0.3.0 apps/api
#
# ---------------------------------------------------------------------------
# NOT YET VERIFIED BY EXECUTION.
# No container runtime (docker, podman, nerdctl, buildah) is installed in the
# environment where this file was written, so neither the build nor a container
# start has been observed to succeed. This is a reviewed definition, not a
# tested artefact. See docs/OPERATIONAL_HARDENING_REPORT.md for exactly which
# steps remain unverified and how to run them.
# ---------------------------------------------------------------------------
#
# Design notes
# ------------
# * Multi-stage: the builder installs dependencies into a virtualenv and the
#   runtime copies only that virtualenv, so no compiler toolchain or build
#   dependencies ship in the final image.
# * Runs as a non-root user. The container has no reason to be root, and a
#   document-ingestion service that parses untrusted files should not be.
# * Dependencies come from the pinned versions in pyproject.toml. The base image
#   is pinned by minor version; pin it by digest for a fully reproducible build.
# * Migrations are applied explicitly by the entrypoint before the server starts,
#   rather than implicitly at import time, so a failed migration stops the
#   container instead of serving traffic against a wrong schema.
# * No .env, .git, local database or blob storage is copied in. .dockerignore
#   enforces this at the build-context level rather than relying on .gitignore.

# --- build stage -----------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build dependencies for wheels that have no compatible prebuilt wheel. Kept in
# the builder only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Copy the dependency metadata first so the dependency layer is cached until
# pyproject.toml actually changes.
#
# Only pyproject.toml and src are copied. There is no README.md in apps/api and
# pyproject.toml declares no readme, so copying one would fail the build -- and
# `pip install .` was verified to succeed without it.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# --- runtime stage ---------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Fail fast rather than buffering logs in a container. Unprefixed: the
    # application has no env_prefix (see .env.example), so MAFHUM_STORAGE_ROOT
    # would be read by nothing and uploads would fall back to ./data relative
    # to /app, outside the declared volume.
    STORAGE_ROOT=/var/lib/mafhum/storage

# psycopg needs the client library at runtime. No build tools, no pip cache.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 mafhum \
    && useradd --system --uid 10001 --gid mafhum --home-dir /var/lib/mafhum \
         --shell /usr/sbin/nologin mafhum \
    && mkdir -p /var/lib/mafhum/storage \
    && chown -R mafhum:mafhum /var/lib/mafhum

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Application code and migration scripts. Deliberately excludes tests, caches,
# .env and any local database or blob data -- see .dockerignore.
COPY --chown=mafhum:mafhum src ./src
COPY --chown=mafhum:mafhum alembic ./alembic
COPY --chown=mafhum:mafhum alembic.ini ./
COPY --chown=mafhum:mafhum entrypoint.sh ./

RUN chmod +x /app/entrypoint.sh

USER mafhum

# Blob storage. Declared as a volume so uploads survive a container restart and
# are never written into the image layer.
VOLUME ["/var/lib/mafhum/storage"]

EXPOSE 8000

# Liveness, not readiness: this only proves the process is serving. Readiness
# (database reachable, storage writable, worker alive) is /health/ready, which the
# orchestrator should use for traffic gating. Both are mounted at the root,
# without the /api/v1 prefix.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/health/live || exit 1

# exec so uvicorn receives SIGTERM directly and can shut down gracefully,
# draining in-flight requests instead of being killed after a timeout.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "mafhum_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
