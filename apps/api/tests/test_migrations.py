"""Migration tests: non-destructive upgrades, schema integrity, and downgrade."""

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.config as config_module


def test_alembic_upgrade_head_applies_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mafhum.db")
        url = f"sqlite:///{db_path}"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        monkeypatch.setattr(config_module.settings, "database_url", url)
        command.upgrade(cfg, "head")

        engine = create_engine(url)
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            tables = set(inspect(connection).get_table_names())
            checks = {
                c["name"] for c in inspect(connection).get_check_constraints("users")
            }
        engine.dispose()

        assert version == "0003_quota_check_constraint"
        assert {"system_settings", "users", "sessions", "projects"}.issubset(tables)
        assert "ck_users_projects_within_quota" in checks


def test_migration_preserves_existing_data_across_0001_to_0003_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mafhum_preserve.db")
        url = f"sqlite:///{db_path}"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        monkeypatch.setattr(config_module.settings, "database_url", url)

        # Apply Phase 1 migration and seed custom existing data.
        command.upgrade(cfg, "0001_initial")
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO system_settings (key, value, description) "
                    "VALUES ('custom_preserve_key', 'important_value', 'must survive 0002+0003')"
                )
            )

        # Upgrade to 0002, seed existing user + owned project with drifted counter,
        # then upgrade to 0003 and verify counter reconciliation + intact user data.
        command.upgrade(cfg, "0002_identity_and_projects")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, preferred_locale, "
                    "is_active, project_quota, projects_count) "
                    "VALUES ('u-1', 'student@univ.dz', 'hash', 'ar', 1, 5, 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO projects (id, owner_id, title, description) "
                    "VALUES ('p-1', 'u-1', 'مشروع التخرج', 'محفوظ عبر الترقية')"
                )
            )

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            val = conn.execute(
                text(
                    "SELECT value FROM system_settings WHERE key = 'custom_preserve_key'"
                )
            ).scalar()
            reconciled_count = conn.execute(
                text("SELECT projects_count FROM users WHERE id = 'u-1'")
            ).scalar()
            project_title = conn.execute(
                text("SELECT title FROM projects WHERE id = 'p-1'")
            ).scalar()
            assert val == "important_value"
            assert reconciled_count == 1
            assert project_title == "مشروع التخرج"

        # Downgrade to 0002 (drops check constraint without dropping user/projects!),
        # then back to 0001_initial, verifying data preservation at each step.
        command.downgrade(cfg, "0002_identity_and_projects")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT title FROM projects WHERE id = 'p-1'")
                ).scalar()
                == "مشروع التخرج"
            )

        command.downgrade(cfg, "0001_initial")
        with engine.connect() as conn:
            val_after_down = conn.execute(
                text(
                    "SELECT value FROM system_settings WHERE key = 'custom_preserve_key'"
                )
            ).scalar()
            assert val_after_down == "important_value"

        command.upgrade(cfg, "head")
        engine.dispose()
