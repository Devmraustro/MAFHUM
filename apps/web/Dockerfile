# MAFHUM web container image.
#
# Build from the repository root:
#   docker build -f apps/web/Dockerfile -t mafhum-web:0.3.0 apps/web
#
# ---------------------------------------------------------------------------
# VERIFIED ON CI (2026-10-27). This image is built and started by
# .github/workflows/deployment-rehearsal.yml on a runner with Docker; run
# 40530008866 concluded success with the web home page and the /health rewrite
# both returning 200. The development sandbox itself has no container runtime,
# so the image was never built there. See docs/DEPLOYMENT_REHEARSAL_REPORT.md.
# ---------------------------------------------------------------------------
#
# Design notes
# ------------
# * Three stages: full deps for the build, production-only deps for the runtime,
#   and a builder that produces `.next`. Dev/test tooling never ships.
# * Runs as a non-root user. A Next.js server only needs to read its build
#   output and open a port.
# * The browser never calls the API host directly. `MAFHUM_API_URL` is a
#   server-side rewrite target read by `next.config.mjs`; it is NOT a public
#   `NEXT_PUBLIC_` variable, so no backend origin is exposed to the client.
# * No `.env`, `.git`, test data or build cache is copied into the runtime
#   stage; `.dockerignore` enforces this at build-context level.

# --- full dependency tree (build only) -------------------------------------
FROM node:22-slim AS deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# --- production dependency tree (runtime only) ------------------------------
FROM node:22-slim AS prod-deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# --- build ------------------------------------------------------------------
FROM node:22-slim AS builder

WORKDIR /app
# next.config.mjs evaluates rewrites() at BUILD time and bakes the destination
# into .next/routes-manifest.json. A runtime-only MAFHUM_API_URL would be
# ignored and the container would proxy to its own 127.0.0.1. Default to the
# Compose service name; override with a build arg for another topology.
ARG MAFHUM_API_URL=http://api:8000
ENV MAFHUM_API_URL=${MAFHUM_API_URL} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# --- runtime ----------------------------------------------------------------
FROM node:22-slim AS runner

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    # Documented for operators; the rewrite target itself is baked at build
    # time from the ARG above (see the builder stage).
    MAFHUM_API_URL=http://api:8000

RUN groupadd --system --gid 10002 mafhum \
    && useradd --system --uid 10002 --gid mafhum --home-dir /app \
         --shell /usr/sbin/nologin mafhum

COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=builder --chown=mafhum:mafhum /app/.next ./.next
COPY --chown=mafhum:mafhum package.json next.config.mjs ./

USER mafhum
EXPOSE 3000

# Node 22 has global fetch, so no curl/wget is needed in the image. This checks
# the rendered home page, not just that a process is listening.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# `npm run start` maps to `next start -H 0.0.0.0 -p 3000` in package.json.
CMD ["npm", "run", "start"]
