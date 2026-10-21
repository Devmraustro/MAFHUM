# Third-party notices

No third-party application code has been copied into the MAFHUM repository as part of the initial documentation baseline. The following are candidate upstream projects reviewed for possible future dependency use; listing them does not mean they are currently integrated:

- ScholaRAGent — https://github.com/ShiYu0318/ScholaRAGent — MIT
- Docling — https://github.com/docling-project/docling — MIT
- PptxGenJS — https://github.com/gitbrent/PptxGenJS — MIT
- OpenTutor — https://github.com/zijinz456/OpenTutor — MIT
- Marp CLI — https://github.com/marp-team/marp-cli — MIT

If any dependency or code is adopted, retain required license texts and copyright notices, pin the version/commit, document modifications, verify transitive dependency licenses, and update this file. Do not infer the model-weight license from its code repository license.

## Phase 1 runtime dependencies (declared in package manifests; not copied into this repository)

These are fetched from npm / PyPI at build time and pinned in `apps/web/package.json` (+ `package-lock.json`) and `apps/api/requirements.txt` / `apps/api/requirements-test.txt`. License assertions below must be verified against each package's LICENSE at build/dependency-audit time; this list is an inventory, not a license grant.

Frontend (`apps/web`): next, react, react-dom (MIT); tailwindcss, @tailwindcss/turbopack (MIT); typescript, eslint, eslint-config-next, @types/* (MIT); vitest, jsdom, @vitejs/plugin-react, vite (MIT); @testing-library/react, @testing-library/dom, @testing-library/jest-dom (MIT).

Backend (`apps/api`): fastapi, pydantic, pydantic-settings (MIT); uvicorn (BSD-3-Clause); sqlalchemy, alembic (MIT); psycopg (LGPLv3 — review linking obligations before distribution); pytest, httpx (MIT); mypy (MIT); ruff (MIT).

No application code was copied from ScholaRAGent, Docling, OpenTutor, PptxGenJS, Marp CLI, Qwen3-Embedding or Haystack in Phase 1; they remain candidates only (see docs/OSS_REUSE_REGISTER.md).
