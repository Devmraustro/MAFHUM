"""The production Compose template must fail closed, on every commit.

A YAML file that parses is not a secure deployment. These tests pin the
invariants that `docker compose config` cannot: a real ClamAV service exists and
is healthy before the API starts, scanning is mandatory, secrets are required
interpolations, and Caddy is the only published port.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "tools" / "acceptance" / "prod_compose_contract.py"


def _load_contract() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prod_compose_contract", CONTRACT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_compose_contract_holds() -> None:
    failures = _load_contract().check_stack()
    assert failures == [], "production Compose contract violations:\n  - " + "\n  - ".join(failures)


def test_contract_checker_fails_when_scanning_is_disabled(tmp_path: Path) -> None:
    """Guard against a contract checker that always returns an empty list."""
    module = _load_contract()
    original = module.COMPOSE
    try:
        # Rewrite a copy of the Compose file with scanning turned off, then point
        # the checker at it. If the checker is a no-op this test fails.
        text = original.read_text(encoding="utf-8")
        broken = tmp_path / "docker-compose.prod.yml"
        broken.write_text(
            text.replace('MALWARE_SCAN_REQUIRED: "true"', 'MALWARE_SCAN_REQUIRED: "false"'),
            encoding="utf-8",
        )
        module.COMPOSE = broken
        violations = module.check_stack()
    finally:
        module.COMPOSE = original
    assert any("MALWARE_SCAN_REQUIRED" in item for item in violations), violations


def test_env_template_ships_only_placeholders() -> None:
    module = _load_contract()
    env = module._env_template()
    for key in ("SESSION_SECRET", "POSTGRES_PASSWORD", "METRICS_SCRAPE_TOKEN"):
        assert env[key].startswith("REPLACE_WITH_"), key
