"""The repository security baseline must hold on every commit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "tools" / "security_baseline.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("security_baseline", BASELINE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_security_baseline_holds() -> None:
    failures = _load().check()
    assert failures == [], "security baseline violations:\n  - " + "\n  - ".join(failures)


def test_secret_scanner_detects_a_planted_token() -> None:
    """A scanner that never fires is worse than no scanner."""
    module = _load()
    planted = ["ghp_" + "A" * 30, "sk-" + "b" * 30]
    for candidate in planted:
        assert any(pattern.search(candidate) for _, pattern in module.SECRET_PATTERNS), candidate
    assert not any(
        pattern.search("REPLACE_WITH_A_LONG_RANDOM_PASSWORD")
        for _, pattern in module.SECRET_PATTERNS
    )
