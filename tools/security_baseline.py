"""Repository security baseline: a deterministic, no-network CI gate.

This is not a substitute for a penetration test or a container image scan. It
is the set of checks that can be proven on every commit without credentials, a
Docker daemon or outbound network access:

* no credential-shaped string in tracked configuration,
* no floating `:latest` container image in a deployment definition,
* every workflow declares read-only permissions and parses as YAML,
* the `.gitignore` really excludes secret files,
* the production Compose fail-closed contract holds,
* no obviously dangerous shell execution primitive in application source,
* shell scripts and container definitions are LF on disk, pinned by
  `.gitattributes`, so a Windows checkout cannot ship CRLF into a container.

Usage:
    python tools/security_baseline.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools" / "acceptance"))

from prod_compose_contract import check_stack  # noqa: E402

# Credential shapes. Deliberately narrow: a broad regex flags the documentation
# that explains what a placeholder looks like, training reviewers to ignore it.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "PEM private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("hard-coded Fernet key", re.compile(r"['\"][A-Za-z0-9_-]{43}=['\"]")),
)

# Files that may contain high-entropy *examples* but must still be scanned.
CONFIG_GLOBS: tuple[str, ...] = (
    ".env.example",
    "deploy/.env.prod.example",
    "deploy/*.yml",
    "deploy/**/*.yml",
    "deploy/**/*.conf",
    "apps/web/vercel.json",
    "apps/web/next.config.mjs",
    ".github/workflows/*.yml",
    "docker-compose.yml",
)

LATEST_IMAGE = re.compile(r"image:\s*[^\s#]*?:latest\b")
# Word boundaries matter: without them `catalog_from_retrieval(` contains the
# literal substring `eval(` and would be reported as a dangerous call.
DANGEROUS_CALLS = re.compile(r"\bos\.system\s*\(|\bshell\s*=\s*True|\beval\s*\(")
REQUIRED_DOCS = (
    "SECURITY.md",
    "docs/THREAT_MODEL.md",
    "docs/DATA_POLICY.md",
    "docs/PENTEST_READINESS.md",
    "docs/ADR/0006-encryption-at-rest.md",
)


def _tracked_files() -> list[Path]:
    git = shutil.which("git")
    if not git:
        raise FileNotFoundError("git is required to enumerate tracked files")
    # Fixed argv with no caller-controlled input; this only enumerates the index.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPO_ROOT / Path(raw.decode()) for raw in result.stdout.split(b"\x00") if raw
    ]


def _config_files(tracked: list[Path]) -> list[Path]:
    selected: list[Path] = []
    for path in tracked:
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in CONFIG_GLOBS:
            selected.append(path)
            continue
        if relative.startswith(("deploy/", ".github/workflows/")) and relative.endswith(
            (".yml", ".yaml", ".conf")
        ):
            selected.append(path)
    return sorted(set(selected))


def check() -> list[str]:
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    try:
        tracked = _tracked_files()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return [f"cannot enumerate tracked files: {exc}"]

    # 1. Credential-shaped strings in tracked configuration.
    scanned = 0
    for path in _config_files(tracked):
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        for label, pattern in SECRET_PATTERNS:
            require(
                pattern.search(text) is None,
                f"{path.relative_to(REPO_ROOT)}: possible {label}",
            )
    require(scanned >= 5, f"only {scanned} config files scanned — glob drift?")

    # 2. No floating image tags in deployment definitions.
    for path in tracked:
        relative = path.relative_to(REPO_ROOT).as_posix()
        if not (relative.endswith(".yml") or relative.endswith(".yaml")):
            continue
        if not (
            relative.startswith("deploy/") or relative.startswith(".github/workflows/")
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        require(
            LATEST_IMAGE.search(text) is None, f"{relative}: unpinned ':latest' image"
        )

    # 3. Workflows parse, and none requests write permission.
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        permissions = data.get("permissions")
        require(
            permissions == "read-all"
            or (
                isinstance(permissions, dict) and permissions.get("contents") == "read"
            ),
            f"{path.name}: permissions must be read-only (contents: read)",
        )
        # PyYAML 1.1 parses the bare key `on` as boolean True; accept either.
        require("on" in data or True in data, f"{path.name}: missing `on` trigger")

    # 4. .gitignore really excludes secret material.
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "secrets/", "*.key", "*.pem"):
        require(entry in gitignore, f".gitignore must exclude {entry}")

    # 4b. Direct Python dependencies are exactly pinned. A range here would let
    # two deployments of the same commit resolve different code.
    pyproject = tomllib.loads(
        (REPO_ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    )
    direct = list(pyproject["project"].get("dependencies", [])) + list(
        pyproject["project"].get("optional-dependencies", {}).get("dev", [])
    )
    for spec in direct:
        name = re.split(r"[<>=!~\[]", spec, maxsplit=1)[0].strip()
        if name.startswith("#") or not name:
            continue
        require("==" in spec, f"unpinned direct Python dependency: {spec}")

    # 4c. No action follows a moving branch reference.
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        require(
            re.search(r"uses:\s*\S+@(?:main|master)\b", text) is None,
            f"{path.name}: an action is pinned to a moving branch",
        )

    # 5. Application source does not invoke a raw shell.
    src_root = REPO_ROOT / "apps" / "api" / "src"
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in DANGEROUS_CALLS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            # Docstrings may legitimately discuss these patterns; only code
            # lines are checked. This is a coarse guard, not a SAST engine.
            source_line = text.splitlines()[line - 1].lstrip()
            if not source_line.startswith(("#", '"', "'")):
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}:{line}: dangerous shell primitive"
                )

    # 6. Security documentation exists.
    for name in REQUIRED_DOCS:
        require((REPO_ROOT / name).is_file(), f"missing required security doc: {name}")

    # 7. Production fail-closed contract.
    for violation in check_stack():
        failures.append(f"compose contract: {violation}")

    # 8. Linux-consumed text files are LF on disk and pinned by .gitattributes.
    #    A Windows checkout made with the installer default `core.autocrlf=true`
    #    converts LF files to CRLF; inside a container that turns a shell
    #    script's shebang into `#!/bin/sh\r`, which the kernel refuses to
    #    execute — observed live on 2026-10-30 (defect D1 in
    #    docs/LOCAL_WINDOWS_VERIFICATION_REPORT.md). The byte scan below fails
    #    on any such checkout until it is re-smudged; the attribute check
    #    keeps a FRESH clone correct, so the fix is preventive, not cosmetic.
    gitattributes_path = REPO_ROOT / ".gitattributes"
    require(
        gitattributes_path.is_file(),
        "missing .gitattributes (LF policy for Linux-consumed files)",
    )
    if gitattributes_path.is_file():
        attributes = gitattributes_path.read_text(encoding="utf-8")
        for pattern, label in (
            (r"(?m)^\*\.sh\b[^#\n]*\beol=lf\b", "*.sh"),
            (r"(?m)^Dockerfile(\.\*)?\b[^#\n]*\beol=lf\b", "Dockerfile"),
        ):
            require(
                re.search(pattern, attributes) is not None,
                f".gitattributes must pin {label} to eol=lf (Windows checkouts must stay LF)",
            )
    for path in tracked:
        relative = path.relative_to(REPO_ROOT).as_posix()
        name = path.name
        enforced = (
            name.endswith((".sh", ".dockerfile"))
            or name == "Caddyfile"
            or name == "Dockerfile"
            or name.startswith("Dockerfile.")
            or (relative.startswith("deploy/clamd/") and name.endswith(".conf"))
        )
        if not enforced:
            continue
        if b"\r" in path.read_bytes():
            failures.append(
                f"{relative}: contains CR bytes — /bin/sh and Linux containers "
                "would break. Re-check the file out so .gitattributes applies "
                "(docs/WINDOWS_SETUP.md §1)."
            )

    return failures


def main() -> int:
    failures = check()
    if failures:
        print(f"RESULT: {len(failures)} SECURITY BASELINE VIOLATION(S)", flush=True)
        for item in failures:
            print(f"  - {item}", flush=True)
        return 1
    print("RESULT: ALL SECURITY BASELINE CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
