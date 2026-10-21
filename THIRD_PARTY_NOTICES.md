# Third-Party Notices

This repository tracks third-party open-source packages declared via standard package manifests (`apps/web/package.json`, `apps/web/package-lock.json`, `apps/api/pyproject.toml`, `apps/api/requirements.txt`, `apps/api/requirements-test.txt`). No third-party source code repositories were copied wholesale into this repository in Phases 0–2.

If any external code, asset, or model weight is adopted in future phases:
1. Retain required upstream license texts and copyright notices here.
2. Pin exact versions/commits in manifests and record the review in `docs/OSS_REUSE_REGISTER.md`.
3. Document any local modifications.
4. Review model-weight licenses separately from source-code licenses.

## Declared dependencies (Phases 1–2 + Phase 2 Security Hardening)

### Backend (`apps/api/requirements.txt` & `requirements-test.txt`)
- `fastapi==0.159.0` — MIT License (`https://github.com/fastapi/fastapi`)
- `starlette==1.9.0` — BSD-3-Clause License (`https://github.com/encode/starlette`)
- `pydantic==2.11.1` — MIT License (`https://github.com/pydantic/pydantic`)
- `pydantic-settings==2.8.1` — MIT License (`https://github.com/pydantic/pydantic-settings`)
- `uvicorn==0.35.0` — BSD-3-Clause License (`https://github.com/encode/uvicorn`)
- `sqlalchemy==2.0.39` — MIT License (`https://github.com/sqlalchemy/sqlalchemy`)
- `alembic==1.20.0` — MIT License (`https://github.com/sqlalchemy/alembic`)
- `psycopg==3.2.7` — LGPL-3.0 License (`https://github.com/psycopg/psycopg`; dynamic package dependency only, not modified or statically linked; review distribution obligations prior to binary redistribution)
- `bcrypt==4.3.0` (`pyca/bcrypt`) — Apache-2.0 License (`https://github.com/pyca/bcrypt`)
- Test/audit dev dependencies (`requirements-test.txt` only, excluded from production runtime):
  - `pytest==9.1.1` — MIT License
  - `httpx==0.28.1` — BSD-3-Clause License
  - `mypy==1.15.0` — MIT License
  - `ruff==0.11.9` — MIT License
  - `pip-audit==2.10.1` — Apache-2.0 License
  - `pgserver==0.1.4` (`https://github.com/orm011/pgserver`) — Apache-2.0 License (Python wrapper) + PostgreSQL License (bundled PostgreSQL 16 server binaries used strictly in offline local test fixtures)

### Frontend (`apps/web/package.json`)
- Runtime dependencies: `next@16.4.2`, `react@19.3.0`, `react-dom@19.3.0` — MIT License.
- Build/test dev dependencies: `tailwindcss@^4`, `@tailwindcss/turbopack@^4`, `typescript@^5`, `eslint@^9`, `eslint-config-next@16.4.2`, `@types/node@^22`, `@types/react@^19`, `@types/react-dom@^19`, `vitest@^5.0.5`, `vite@^8.3.5`, `@vitejs/plugin-react@^6.1.2`, `jsdom@^30.1.3`, `@testing-library/react@^16.3.3`, `@testing-library/dom@^10.4.2`, `@testing-library/jest-dom@^7.0.1` — MIT License (with BSD-3-Clause transitive utilities in `jsdom` ecosystem).

## Unadopted / candidate components
No code was copied or installed from ScholaRAGent, Docling, OpenTutor, PptxGenJS, Marp CLI, Qwen3-Embedding, or Haystack in Phases 0–2. See `docs/OSS_REUSE_REGISTER.md` for full evaluation notes including declined candidates (`PyJWT`, `slowapi`, `fastapi-users`).
