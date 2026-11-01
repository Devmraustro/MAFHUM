"""Vercel Function entrypoint (Phase 1 of the Vercel-native migration).

Vercel's Python runtime loads the ``app`` variable from this file (a
recognized entrypoint location, also pinned explicitly via
``tool.vercel.entrypoint`` in ``pyproject.toml``). All it does is make the
``src/`` layout importable and re-export the serverless application built by
:mod:`mafhum_api.vercel_app` — no app logic lives here.

Kept as a plain shim so the deployment wiring stays reviewable in one screen:
worker threads are disabled and the PostgreSQL-only guard runs inside
``mafhum_api.vercel_app``, under the same ruff/mypy gates as the rest of the
package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from mafhum_api.vercel_app import app  # noqa: E402 - after the src/ path setup

__all__ = ["app"]
