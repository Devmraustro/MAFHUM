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
        engine.dispose()

        assert version == "0002_identity_and_projects"
        assert {"system_settings", "users", "sessions", "projects"}.issubset(tables)


def test_migration_preserves_existing_data_across_0001_to_0002_and_downgrade(
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
                    "VALUES ('custom_preserve_key', 'important_value', 'must survive 0002')"
                )
            )

        # Upgrade to head (Phase 2) and verify pre-existing data is intact.
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            val = conn.execute(
                text("SELECT value FROM system_settings WHERE key = 'custom_preserve_key'")
            ).scalar()
            assert val == "important_value"

        # Downgrade back to 0001_initial and verify system_settings still intact, then re-upgrade.
        command.downgrade(cfg, "0001_initial")
        with engine.connect() as conn:
            val_after_down = conn.execute(
                text("SELECT value FROM system_settings WHERE key = 'custom_preserve_key'")
            ).scalar()
            assert val_after_down == "important_value"

        command.upgrade(cfg, "head")
        engine.dispose()
