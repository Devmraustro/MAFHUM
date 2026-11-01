"""Phase 1 Vercel deployment regression tests.

These pin the serverless deployment contract from
``docs/VERCEL_NATIVE_MIGRATION_PLAN.md``:

- the Vercel entrypoint exposes the existing FastAPI app with the in-process
  worker disabled (no threads at cold start, even when the env var says
  otherwise);
- PostgreSQL is the only accepted database — a non-PostgreSQL URL aborts
  instead of silently falling back;
- the connection pool keeps operator-supplied bounds and supports a pre-ping
  for stale serverless connections, and the lifespan disposes the engine;
- nothing in the cold start runs migrations or any other DDL: the function
  serves against an existing migrated schema;
- the security surface is unchanged: security headers, session cookies, the
  Origin allowlist and the CSRF double-submit all behave exactly as in the
  container deployment;
- readiness reports the honest serverless state (``processing_worker``:
  ``stopped``), never a fabricated ``ok``.

All tests are hermetic: real PostgreSQL via the repo's pgserver fixture and
TestClient — no network, no Vercel account, no production identifiers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from mafhum_api.config import Settings
from mafhum_api.vercel_app import assert_postgres_only, create_vercel_app

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_postgres_url(postgres_url: str, _migrated: str) -> str:
    """The session database with the real Alembic schema applied.

    The app itself never runs migrations; the schema is created here by the
    test harness, exactly like a controlled release step would.
    """
    return postgres_url


def vercel_settings(**overrides: Any) -> Settings:
    """Deployment-shaped settings on top of the migrated test database."""
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": overrides.pop("database_url"),
        "storage_root": overrides.pop("storage_root"),
        "session_secret": "test-secret-not-for-production",
        "cookie_secure": False,
        "allowed_origins": ["http://testserver", "http://localhost:3000"],
        # Serverless-shaped pool: small, bounded, pre-ping on.
        "db_pool_size": 1,
        "db_max_overflow": 1,
        "db_pool_pre_ping": True,
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------- entrypoint
def test_vercel_entrypoint_shim_exports_the_serverless_app() -> None:
    """api/index.py (the file Vercel loads) re-exports mafhum_api.vercel_app.

    Loaded from its real path exactly the way the runtime would, so the
    sys.path shim itself is under test.
    """
    spec = importlib.util.spec_from_file_location(
        "mafhum_api_vercel_shim", API_ROOT / "api" / "index.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import mafhum_api.vercel_app

    assert module.app is mafhum_api.vercel_app.app
    # The shim made the src/ layout importable.
    assert str(API_ROOT / "src") in sys.path


def test_vercel_project_config_pins_the_entrypoint_and_trims_the_bundle() -> None:
    """pyproject/vercel.json keep the deployment wiring explicit and lean."""
    import tomllib

    with (API_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    assert pyproject["tool"]["vercel"]["entrypoint"] == "api.index:app"

    import json

    config = json.loads((API_ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert "api/index.py" in config["functions"]
    excluded = config["functions"]["api/index.py"]["excludeFiles"]
    for fragment in ("tests/**", "alembic/**", ".venv/**"):
        assert fragment in excluded, fragment


def test_vercel_app_is_the_standard_app_with_worker_disabled(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """Same factory, same middleware/routers — only the worker is off."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)

    assert app.state.serverless_deployment is True
    context = app.state.context
    assert context.worker.is_running is False  # nothing started at build time
    assert context.database.dialect == "postgresql"
    # The full API surface is mounted, not a reduced POC router: the OpenAPI
    # schema (enabled outside production) exposes every real route.
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        paths = set(schema["paths"])
        assert "/health/live" in paths
        assert "/health/ready" in paths
        assert settings.api_prefix + "/auth/register" in paths


# ------------------------------------------------------------ no worker start
def test_cold_start_starts_no_worker_threads_even_when_env_allows_it(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """start_worker=False is a code-level guarantee: PROCESSING_WORKER_ENABLED
    cannot accidentally spawn threads inside a serverless instance. The
    declared state is forced to match reality too — otherwise the tick (the
    serverless driver) would refuse to run while no worker exists, leaving
    the deployment with no driver at all."""
    settings = vercel_settings(
        database_url=migrated_postgres_url,
        storage_root=storage_root,
        processing_worker_enabled=True,
    )
    app = create_vercel_app(settings)
    with TestClient(app) as client:  # lifespan runs, like a function start
        context = app.state.context
        assert context.worker.is_running is False  # and nothing started on shutdown
        # Env cannot spawn threads (start_worker=False) AND cannot wedge the
        # tick: the serverless path declares the driver it actually has.
        assert context.settings.processing_worker_enabled is False
        assert client.get("/health/live").status_code == 200
    assert context.worker.is_running is False  # and nothing started on shutdown


# ------------------------------------------------------------------ postgres
@pytest.mark.parametrize("bad_url", ["sqlite:///tmp/mafhum.db", "mysql://u:p@h/db", ""])
def test_non_postgres_database_url_aborts_the_cold_start(bad_url: str) -> None:
    """No silent fallback: anything but PostgreSQL refuses to boot."""
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        assert_postgres_only(Settings(database_url=bad_url, app_env="test"))


def test_plain_postgresql_scheme_is_accepted_as_postgres() -> None:
    """pgserver- and Neon-style postgresql:// URLs are PostgreSQL too."""
    assert_postgres_only(Settings(database_url="postgresql://u:p@h:5432/db", app_env="test"))


def test_sqlite_is_never_substituted_by_the_vercel_path(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """A valid PostgreSQL URL boots the serverless app unchanged (guard passes)."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    assert app.state.context.database.engine.dialect.name == "postgresql"


# ------------------------------------------------------- bounded pool lifecycle
def test_pool_bounds_and_pre_ping_are_applied(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_PRE_PING reach the engine."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    pool = app.state.context.database.engine.pool
    assert pool.size() == 1
    assert getattr(pool, "_max_overflow", 0) == 1
    # QueuePool internal flag, stable across SQLAlchemy 2.x: proves the
    # stale-connection pre-ping requested by the deployment is active.
    assert pool._pre_ping is True  # type: ignore[attr-defined]


def test_default_settings_keep_the_container_pool_behaviour() -> None:
    """The pre-ping is opt-in: the existing deployment path is unchanged."""
    from mafhum_api.db.base import Database

    default = Database("postgresql+psycopg://u:p@localhost:5432/x")
    try:
        assert default.engine.pool._pre_ping is False  # type: ignore[attr-defined]
    finally:
        default.dispose()


def test_lifespan_disposes_the_engine_on_shutdown(
    migrated_postgres_url: str, storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Function shutdown returns connections instead of leaking them."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    disposed: list[bool] = []
    original = app.state.context.database.dispose

    def record_dispose() -> None:
        disposed.append(True)
        original()

    monkeypatch.setattr(app.state.context.database, "dispose", record_dispose)
    with TestClient(app):
        assert disposed == []  # not disposed while serving
    assert disposed == [True]


# ------------------------------------------------- no migrations at cold start
def test_cold_start_runs_no_ddl_and_no_migrations(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """Startup + a request against an existing schema issue no DDL at all.

    Migrations are a controlled release step; a Function cold start must
    never mutate the schema. Every statement the engine emits during the
    lifespan is recorded and checked.
    """
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    statements: list[str] = []

    def before_cursor_execute(
        conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any
    ) -> None:
        statements.append(statement.upper())

    event.listen(app.state.context.database.engine, "before_cursor_execute", before_cursor_execute)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health/live").status_code == 200

    forbidden = ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "DROP INDEX")
    for statement in statements:
        for ddl in forbidden:
            assert not statement.startswith(ddl), (ddl, statement[:80])


def test_vercel_entrypoint_sources_reference_no_alembic() -> None:
    """The deployment modules never import or invoke the migration tooling
    (prose may mention it; code must not)."""
    import ast

    for relative in ("src/mafhum_api/vercel_app.py", "api/index.py"):
        tree = ast.parse((API_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("alembic") for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("alembic"), relative


# ------------------------------------------------------------------- health
def test_health_endpoints_report_the_honest_serverless_state(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """Liveness is ok; readiness reports the real dependency states — the
    database is reachable, the in-process worker is honestly `stopped`."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "ok"

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        checks = ready.json()["checks"]
        assert checks["database"] == "ok"
        assert checks["processing_worker"] == "stopped"  # never fabricated
        assert ready.json()["status"] == "degraded"  # degraded, not ok — honest


# ------------------------------------------------------- auth / cookies / CSRF
def _register_and_login(client: TestClient, email: str) -> dict[str, str]:
    origin = "http://testserver"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Correct-Horse-4"},
        headers={"Origin": origin},
    )
    assert registered.status_code == 201, registered.text
    csrf = client.cookies.get("mafhum_csrf")
    assert csrf
    return {"Origin": origin, "X-CSRF-Token": csrf}


def test_authentication_and_csrf_survive_the_vercel_wiring(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """Register/login set the session + CSRF cookies; the double-submit and
    the Origin allowlist behave exactly as in the container deployment."""
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    with TestClient(app) as client:
        headers = _register_and_login(client, "vercel-poc@example.com")
        assert client.cookies.get("mafhum_session")

        # A mutation without the CSRF header is rejected.
        no_token = client.post(
            "/api/v1/projects",
            json={"title": "poc"},
            headers={"Origin": "http://testserver"},
        )
        assert no_token.status_code == 422
        assert no_token.json()["code"] == "csrf_failed"

        # A mutation from a foreign Origin is rejected even with a token.
        foreign = client.post(
            "/api/v1/projects",
            json={"title": "poc"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": headers["X-CSRF-Token"]},
        )
        assert foreign.status_code == 422
        assert foreign.json()["code"] == "csrf_failed"

        # With both proofs the mutation succeeds.
        ok = client.post("/api/v1/projects", json={"title": "poc"}, headers=headers)
        assert ok.status_code == 201, ok.text


def test_security_headers_are_present_on_the_vercel_path(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    with TestClient(app) as client:
        response = client.get("/health/live")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"


def test_unauthenticated_requests_are_rejected(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    settings = vercel_settings(database_url=migrated_postgres_url, storage_root=storage_root)
    app = create_vercel_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/documents", headers={"Origin": "http://testserver"})
        assert response.status_code == 401


# ------------------------------------------------- web app must stay untouched
def test_nextjs_proxy_configuration_is_unchanged() -> None:
    """The web app keeps its same-origin /api proxy: the Vercel API project is
    a separate deployment, not a rewrite of the frontend's routing."""
    web_config = (API_ROOT.parent / "web" / "next.config.mjs").read_text(encoding="utf-8")
    assert '"/api/:path*"' in web_config
    assert "MAFHUM_API_URL" in web_config
    # And the web project's own vercel.json is untouched by this phase.
    import json

    web_vercel = json.loads((API_ROOT.parent / "web" / "vercel.json").read_text(encoding="utf-8"))
    assert web_vercel["framework"] == "nextjs"


# ------------------------------------------------- managed-Postgres (Neon) wiring
def _engine_connect_timeout(pool: object) -> int | None:
    """Recover the psycopg connect_timeout from the pool creator closure."""
    creator = getattr(pool, "_creator", None)
    for cell in creator.__closure__ or []:
        contents = cell.cell_contents
        if isinstance(contents, dict) and "connect_timeout" in contents:
            return contents["connect_timeout"]
    return None


def test_managed_postgres_engine_bounds_and_timeouts(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """Neon-shaped settings reach the engine: bounded pool, pre-ping,
    connect timeout, and optional connection recycling."""
    settings = vercel_settings(
        database_url=migrated_postgres_url,
        storage_root=storage_root,
        db_connect_timeout_seconds=7,
        db_pool_recycle_seconds=240,
    )
    app = create_vercel_app(settings)
    pool = app.state.context.database.engine.pool
    assert pool.size() == 1
    assert pool._pre_ping is True  # type: ignore[attr-defined]
    assert pool._recycle == 240  # type: ignore[attr-defined]
    assert _engine_connect_timeout(pool) == 7


def test_container_default_engine_ignores_recycle_and_keeps_timeouts() -> None:
    """Defaults preserve the long-lived-container pool exactly, except that an
    unreachable database now fails fast instead of hanging the request."""
    from mafhum_api.db.base import Database

    default = Database("postgresql+psycopg://u:p@localhost:5432/x")
    try:
        assert default.engine.pool._recycle == -1  # type: ignore[attr-defined]
        assert default.engine.pool._pre_ping is False  # type: ignore[attr-defined]
        assert _engine_connect_timeout(default.engine.pool) == 10
    finally:
        default.dispose()


def test_serverless_entrypoint_leaves_the_tick_usable(
    migrated_postgres_url: str, storage_root: Path
) -> None:
    """On the serverless path the tick is the processing driver; nothing in
    the cold start may disable it (the single-driver guard must see no
    resident worker)."""
    settings = vercel_settings(
        database_url=migrated_postgres_url,
        storage_root=storage_root,
        processing_worker_enabled=True,  # even a misconfigured env
    )
    app = create_vercel_app(settings)
    with TestClient(app) as client:
        register = client.post(
            settings.api_prefix + "/auth/register",
            json={
                "email": "tickable@example.com",
                "password": "Passw0rd!23",
                "display_name": "tickable",
            },
            headers={"Origin": "http://testserver"},
        )
        assert register.status_code == 201, register.text
        csrf = client.get(settings.api_prefix + "/auth/session").json()["csrf_token"]
        tick = client.post(
            settings.api_prefix + "/processing/tick",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        # Not the single-driver 409: the tick ran (empty queue is fine).
        assert tick.status_code == 200, tick.text
        assert tick.json()["stopped_reason"] == "drained"
