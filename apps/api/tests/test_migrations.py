"""Migration smoke test: Alembic must apply offline against SQLite."""

import os
import tempfile

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_head_applies_offline(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mafhum.db")
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        # The Alembic env reads settings.database_url at runtime; point it at
        # the temp database so both upgrade and verification hit the same file.
        import app.config as config_module

        monkeypatch.setattr(config_module.settings, "database_url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

        # Verify the version table recorded the applied revision.
        from sqlalchemy import create_engine, text

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
        assert row == "0001_initial"
