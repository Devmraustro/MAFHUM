"""Real PostgreSQL database portability, migration, constraint, and concurrency tests.

Executes against a live PostgreSQL server (CI service container when
`TEST_POSTGRES_URL` is set, or embedded PostgreSQL 16 via `pgserver` locally)
to verify real production database semantics rather than relying solely on SQLite.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SaSession

import app.api.deps as deps_module
import app.config as config_module
from app.main import app
from app.models.project import Project
from app.models.session import Session as SessionModel
from app.models.user import User


def _alembic_cfg_for(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_postgresql_migrations_upgrade_downgrade_and_data_preservation(
    postgres_empty_db: str,
) -> None:
    """Run full Alembic upgrade -> downgrade -> upgrade cycle on an empty PostgreSQL DB."""
    cfg = _alembic_cfg_for(postgres_empty_db)

    # Confirm database is truly PostgreSQL and completely empty initially.
    engine = create_engine(postgres_empty_db)
    with engine.connect() as conn:
        assert conn.dialect.name == "postgresql"
        pg_ver = conn.execute(text("SHOW server_version")).scalar_one()
        assert int(str(pg_ver).split(".")[0]) >= 15
        assert inspect(conn).get_table_names() == []

    # Step 1: upgrade empty DB to head.
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        version = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert version == "0003_quota_check_constraint"
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        assert {"system_settings", "users", "sessions", "projects"} <= tables

        user_checks = {c["name"] for c in inspector.get_check_constraints("users")}
        assert {
            "ck_users_project_quota_nonneg",
            "ck_users_projects_count_nonneg",
            "ck_users_projects_within_quota",
            "ck_users_preferred_locale",
        } <= user_checks

        proj_fks = inspector.get_foreign_keys("projects")
        assert any(
            fk["referred_table"] == "users"
            and fk.get("options", {}).get("ondelete") == "CASCADE"
            for fk in proj_fks
        )
        sess_fks = inspector.get_foreign_keys("sessions")
        assert any(
            fk["referred_table"] == "users"
            and fk.get("options", {}).get("ondelete") == "CASCADE"
            for fk in sess_fks
        )

    # Step 2: downgrade completely back to `base`, confirming clean PostgreSQL teardown.
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        remaining = set(inspect(conn).get_table_names()) - {"alembic_version"}
        assert remaining == set()

    # Step 3: upgrade to 0002, seed live user + owned project with stale projects_count=0,
    # upgrade to 0003, downgrade to 0002, and re-upgrade to head without losing any data.
    command.upgrade(cfg, "0002_identity_and_projects")
    uid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, preferred_locale, "
                "is_active, project_quota, projects_count) "
                "VALUES (:uid, 'pg-preserve@univ-setif.dz', 'hash', 'ar', true, 5, 0)"
            ),
            {"uid": uid},
        )
        conn.execute(
            text(
                "INSERT INTO projects (id, owner_id, title, description) "
                "VALUES (:pid, :uid, 'مشروع محفوظ على PostgreSQL', 'اختبار الترقية')"
            ),
            {"pid": pid, "uid": uid},
        )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        count_val = conn.execute(
            text("SELECT projects_count FROM users WHERE id = :uid"), {"uid": uid}
        ).scalar_one()
        title_val = conn.execute(
            text("SELECT title FROM projects WHERE id = :pid"), {"pid": pid}
        ).scalar_one()
        assert count_val == 1
        assert title_val == "مشروع محفوظ على PostgreSQL"

    command.downgrade(cfg, "0002_identity_and_projects")
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT title FROM projects WHERE id = :pid"), {"pid": pid}
            ).scalar_one()
            == "مشروع محفوظ على PostgreSQL"
        )
    engine.dispose()


def test_postgresql_constraints_cascades_and_session_lifecycle(
    postgres_empty_db: str,
) -> None:
    """Verify FKs, unique/check constraints, ON DELETE CASCADE, and session revocation on PG."""
    cfg = _alembic_cfg_for(postgres_empty_db)
    command.upgrade(cfg, "head")

    engine = create_engine(postgres_empty_db)
    uid = str(uuid.uuid4())

    # 1. Valid parent user insert works; invalid locale / quota / count check constraints fail.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, preferred_locale, "
                "is_active, project_quota, projects_count) "
                "VALUES (:uid, 'constraints@univ-alger.dz', 'hash', 'ar', true, 2, 0)"
            ),
            {"uid": uid},
        )

    # Duplicate email violates UNIQUE constraint.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, preferred_locale, "
                "is_active, project_quota, projects_count) "
                "VALUES (:uid, 'constraints@univ-alger.dz', 'hash', 'ar', true, 2, 0)"
            ),
            {"uid": str(uuid.uuid4())},
        )

    # `projects_count > project_quota` violates `ck_users_projects_within_quota`.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET projects_count = 3 WHERE id = :uid"),
            {"uid": uid},
        )

    # Orphan foreign key on `projects.owner_id` violates FK constraint.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, owner_id, title) "
                "VALUES (:pid, :missing_uid, 'Orphan')"
            ),
            {"pid": str(uuid.uuid4()), "missing_uid": str(uuid.uuid4())},
        )

    # Native PostgreSQL `ON DELETE CASCADE` deletes child sessions & projects when parent row deleted.
    sid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sessions (id, user_id, token_hash, expires_at, revoked) "
                "VALUES (:sid, :uid, :th, :exp, false)"
            ),
            {
                "sid": sid,
                "uid": uid,
                "th": "h" * 64,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
        )
        conn.execute(
            text("INSERT INTO projects (id, owner_id, title) VALUES (:pid, :uid, 'Cascade')"),
            {"pid": pid, "uid": uid},
        )
        conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM sessions WHERE id = :sid"), {"sid": sid}
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM projects WHERE id = :pid"), {"pid": pid}
            ).scalar_one()
            == 0
        )

    # Exercise full HTTP auth + session revocation + expiry against PostgreSQL.
    dev1 = TestClient(app)
    dev2 = TestClient(app)
    reg = dev1.post(
        "/api/v1/auth/register",
        json={
            "email": "pg-auth@univ-setif.dz",
            "password": "كلمة-مرور-قوية-جدا-2026",
            "preferred_locale": "ar",
        },
    )
    assert reg.status_code == 201
    login2 = dev2.post(
        "/api/v1/auth/login",
        json={"email": "pg-auth@univ-setif.dz", "password": "كلمة-مرور-قوية-جدا-2026"},
    )
    assert login2.status_code == 200

    sessions = dev1.get("/api/v1/auth/sessions").json()["items"]
    assert len(sessions) == 2
    other_session_id = next(s["id"] for s in sessions if not s["is_current"])

    # Revoke device 2 from device 1 -> device 2 immediately gets 401 on PostgreSQL!
    csrf1 = dev1.cookies.get("mafhum_csrf") or ""
    assert (
        dev1.delete(
            f"/api/v1/auth/sessions/{other_session_id}",
            headers={"X-CSRF-Token": csrf1},
        ).status_code
        == 200
    )
    assert dev2.get("/api/v1/me").status_code == 401
    assert dev1.get("/api/v1/me").status_code == 200

    # Expire device 1's session timestamp directly in PostgreSQL -> immediate 401!
    with SaSession(engine) as db:
        active_sess = db.execute(
            select(SessionModel).where(SessionModel.revoked.is_(False))
        ).scalar_one()
        active_sess.expires_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()
    assert dev1.get("/api/v1/me").status_code == 401
    engine.dispose()


def test_postgresql_concurrent_project_quota_with_select_for_update(
    postgres_empty_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """12 simultaneous PostgreSQL connections enforce exact quota=3 via SELECT ... FOR UPDATE."""
    cfg = _alembic_cfg_for(postgres_empty_db)
    command.upgrade(cfg, "head")
    monkeypatch.setattr(config_module.settings, "default_project_quota", 3)

    setup = TestClient(app)
    reg = setup.post(
        "/api/v1/auth/register",
        json={
            "email": "pg-concurrency@univ-constantine.dz",
            "password": "كلمة-مرور-متزامنة-قوية-2026",
            "preferred_locale": "ar",
        },
    )
    assert reg.status_code == 201
    uid = str(reg.json()["id"])
    sess_token = setup.cookies.get("mafhum_session") or ""
    csrf_token = setup.cookies.get("mafhum_csrf") or ""

    workers = 12
    barrier = threading.Barrier(workers)

    def _pg_worker(idx: int) -> tuple[int, dict[str, Any]]:
        wc = TestClient(app)
        wc.cookies.set("mafhum_session", sess_token)
        wc.cookies.set("mafhum_csrf", csrf_token)
        barrier.wait(timeout=15)
        resp = wc.post(
            "/api/v1/projects",
            json={"title": f"مشروع PostgreSQL متزامن {idx}"},
            headers={"X-CSRF-Token": csrf_token},
        )
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_pg_worker, range(workers)))

    codes = [c for c, _ in results]
    assert 500 not in codes, results
    assert codes.count(201) == 3, results
    assert codes.count(403) == workers - 3, results

    engine = deps_module.get_engine()
    assert engine is not None
    with SaSession(engine) as db:
        owned_count = db.execute(
            select(func.count(Project.id)).where(Project.owner_id == uid)
        ).scalar_one()
        user_row = db.get(User, uid)
        assert user_row is not None
        assert owned_count == 3
        assert user_row.projects_count == 3
