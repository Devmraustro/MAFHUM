# Third-party notices

Every component MAFHUM depends on, with the **verified** license of the exact
pinned version. Licences were checked against PyPI / the npm registry metadata
(`license_expression` where present, otherwise the classifier or the upstream
repository), not assumed from popularity.

Nothing is vendored or copied into this repository; all components are consumed
as declared dependencies.

---

## Python — production runtime

| Package | Pinned version | License | Notes |
|---|---|---|---|
| fastapi | 0.159.1 | MIT | API framework |
| starlette | 1.9.0 | BSD-3-Clause | Pulled in by FastAPI |
| uvicorn[standard] | 0.54.0 | BSD-3-Clause | ASGI server |
| pydantic | 2.12.5 | MIT | Validation |
| pydantic-settings | 2.16.1 | MIT | Configuration |
| SQLAlchemy[postgresql] | 2.1.6 | MIT | ORM / Core |
| alembic | 1.20.0 | MIT | Migrations |
| **psycopg[binary]** | 3.3.6 | **LGPL-3.0-only** | See the note below |
| python-multipart | 0.0.32 | Apache-2.0 | Multipart upload parsing |
| pypdf | 6.21.2 | BSD-3-Clause | PDF text extraction |
| python-docx | 1.2.0 | MIT | DOCX text extraction |
| lxml | 6.1.3 | BSD-3-Clause | Pulled in by python-docx |
| email-validator | 2.3.0 | **Unlicense** (public domain) | Email syntax validation |

### Note on psycopg (LGPL-3.0-only)

`psycopg` is the PostgreSQL driver and is licensed under **LGPL-3.0-only**, which
is weak copyleft. This is a deliberate, reviewed decision:

- MAFHUM imports psycopg as an ordinary Python library and does not modify it.
  LGPL's replacement requirement is satisfied because the dependency is declared
  in `pyproject.toml` and can be substituted by any DBAPI-2 driver.
- LGPL is **not** AGPL or GPL. No network-copyleft obligation is triggered by
  serving MAFHUM over a network.
- The alternative pure-PostgreSQL drivers were either unmaintained or lacked
  cp311 wheels (`postgresql-wheel` ships no wheel for Python 3.11).

If a strictly permissive stack is ever required, this is the single dependency
that would have to change.

### Note on email-validator (Unlicense)

PyPI reports `email-validator` as **Unlicense** (a public-domain dedication),
confirmed by its `License :: OSI Approved :: The Unlicense (Unlicense)`
classifier. This is permissive and imposes no obligation. It is recorded here
explicitly because it is often mistaken for MIT.

---

## Python — development, build and test only

These are **never** part of a production install (`pip install .` excludes them).

| Package | Pinned version | License | Why |
|---|---|---|---|
| pytest | 9.1.1 | MIT | Test runner |
| httpx | 0.28.1 | BSD-3-Clause | HTTP assertions |
| httpx2 | 2.16.0 | BSD-3-Clause | Required by Starlette's `TestClient` |
| ruff | 0.17.1 | MIT | Lint + format |
| mypy | 2.4.0 | MIT | Strict static typing |
| pip-audit | 2.9.0 | Apache-2.0 | Dependency vulnerability scanning |
| fonttools | 4.67.0 | MIT | Builds the synthetic PDF fixtures |
| types-setuptools | 84.0.0.20261006 | MIT | Type stubs |
| **pgserver** | 0.1.4 | **not published** | See the note below |

### Note on pgserver (license not published)

`pgserver` bundles a self-contained PostgreSQL 16.2 binary and is used **only**
by the test suite to start a real database. Its PyPI metadata contains **no
license field and no license classifier**, so its terms are unverified.

It is accepted on that basis only because it is a dev/test dependency that never
ships in the product and never runs in production. If MAFHUM is ever
redistributed as a bundle, `pgserver` must be removed or its licensing clarified
first. This is recorded as an open item rather than glossed over.

---

## Node.js — `apps/web`

All MIT unless stated. Verified against the npm registry.

### Production runtime

| Package | Pinned version | License |
|---|---|---|
| next | 16.4.2 | MIT |
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |

### Development, build and test only

| Package | Pinned version | License |
|---|---|---|
| typescript | 5.9.3 | Apache-2.0 |
| eslint | 10.13.0 | MIT |
| eslint-config-next | 16.4.2 | MIT |
| vitest | 5.0.5 | MIT |
| vite | 8.3.5 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
| @testing-library/react | 16.3.3 | MIT |
| @testing-library/dom | 10.4.2 | MIT |
| jsdom | 30.1.3 | MIT |
| @types/node | 24.10.1 | MIT |
| @types/react | 19.2.7 | MIT |
| @types/react-dom | 19.2.3 | MIT |

`typescript` is pinned to 5.9.3 and additionally held there by an `overrides`
entry, because `typescript-eslint` (via `eslint-config-next`) requires
`>=4.8.4 <6.1.0`. Without the override, npm backtracks indefinitely trying to
upgrade TypeScript and the install never completes.

---

## Fonts

The DejaVu fonts used by the test PDF fixtures come from the host system
(`/usr/share/fonts/truetype/dejavu/`) and are **not** redistributed in this
repository. The fixtures are generated at test time.

## Bundled PostgreSQL

PostgreSQL itself is only present in development, via `pgserver` (see above).
PostgreSQL is licensed under the PostgreSQL License (permissive, MIT-like).
