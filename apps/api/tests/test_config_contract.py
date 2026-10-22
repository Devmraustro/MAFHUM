"""Contract tests between configuration and the files that document it.

These exist because `.env.example` had drifted into documenting variables the
application never reads (`AI_PROVIDER`, `MAX_DOCUMENT_PAGES`) while omitting
`SESSION_SECRET`, without which production refuses to boot. A template that
lies is worse than no template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mafhum_api.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def template_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip().upper())
    return keys


def settings_keys() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


class TestEnvExample:
    def test_template_exists_and_is_not_empty(self) -> None:
        assert ENV_EXAMPLE.is_file()
        assert len(template_keys()) > 20

    def test_every_documented_variable_is_a_real_setting(self) -> None:
        unknown = sorted(template_keys() - settings_keys())
        assert unknown == [], f".env.example documents unknown settings: {unknown}"

    def test_operationally_critical_settings_are_documented(self) -> None:
        required = {"DATABASE_URL", "SESSION_SECRET", "STORAGE_ROOT", "COOKIE_SECURE"}
        missing = sorted(required - template_keys())
        assert missing == [], f".env.example omits critical settings: {missing}"

    def test_template_ships_no_real_secret(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        secret_patterns = [
            r"sk-[A-Za-z0-9]{16,}",
            r"gh[pousr]_[A-Za-z0-9]{20,}",
            r"AKIA[0-9A-Z]{16}",
            r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY",
        ]
        for pattern in secret_patterns:
            assert not re.search(pattern, text), f"possible secret matching {pattern}"

    def test_production_refuses_the_development_secret(self) -> None:
        settings = Settings(app_env="production")
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            settings.assert_production_ready()

    def test_development_allows_the_development_secret(self) -> None:
        Settings(app_env="development").assert_production_ready()
