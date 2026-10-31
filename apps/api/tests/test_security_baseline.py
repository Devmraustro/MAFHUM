"""The repository security baseline must hold on every commit."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "tools" / "security_baseline.py"
GITATTRIBUTES = REPO_ROOT / ".gitattributes"


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


def test_required_lf_rules_hold_in_the_repository() -> None:
    module = _load()
    attributes = GITATTRIBUTES.read_text(encoding="utf-8")
    assert module.missing_lf_rules(attributes) == []


@pytest.mark.parametrize("label", [label for label, _ in _load().REQUIRED_LF_RULES], ids=str)
def test_removing_any_required_lf_rule_fails_the_check(label: str) -> None:
    """Negative test: every rule is individually load-bearing.

    Removes exactly the lines matching that one rule from the real
    `.gitattributes` and asserts the checker attributes the failure to it.
    """
    module = _load()
    pattern = re.compile(dict(module.REQUIRED_LF_RULES)[label])
    lines = GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if pattern.search(line) is None]
    # Guard against a vacuous test: the rule must have matched a real line.
    assert len(kept) < len(lines), f"rule {label!r} matched no line in .gitattributes"
    assert module.missing_lf_rules("\n".join(kept) + "\n") == [label]


def test_lf_byte_scan_scope_matches_the_pinned_rules() -> None:
    """The CR-byte scan must cover exactly the container-consumed file classes."""
    module = _load()
    for relative in (
        "apps/api/entrypoint.sh",
        "deploy/clamd/entrypoint.sh",
        "deploy/clamd/healthcheck.sh",
        "tools/staging_verification.sh",
        "apps/api/Dockerfile",
        "apps/web/Dockerfile",
        "deploy/clamd/Dockerfile",
        "deploy/Caddyfile",
        "deploy/clamd/clamd.conf",
        "deploy/clamd/clamdscan.conf",
        "vendor/lib/anything.dockerfile",
    ):
        assert module._is_lf_enforced(relative), relative
    for relative in (
        "docs/WINDOWS_SETUP.md",
        "docker-compose.yml",
        "deploy/docker-compose.prod.yml",
        "apps/api/src/mafhum_api/config.py",
        "README.md",
        ".gitattributes",
    ):
        assert not module._is_lf_enforced(relative), relative
