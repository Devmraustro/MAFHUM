"""Core API startup must not require the optional HTTP research client."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_app_imports_when_optional_http_clients_are_unavailable() -> None:
    api_root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import sys

class BlockOptionalHttpClients(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx.") or fullname == "httpcore" or fullname.startswith("httpcore."):
            raise ModuleNotFoundError(f"blocked optional dependency: {fullname}")
        return None

sys.meta_path.insert(0, BlockOptionalHttpClients())
import mafhum_api.main
assert "httpx" not in sys.modules
assert "httpcore" not in sys.modules
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(api_root / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=api_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"core API import failed without optional HTTP clients: "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
