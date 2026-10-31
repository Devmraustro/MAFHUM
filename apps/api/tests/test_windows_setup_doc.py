"""Guard rails for the Windows setup instructions.

The one-time line-ending repair in ``docs/WINDOWS_SETUP.md`` §1 re-checks-out
the LF-enforced files. A review of PR #7 flagged that the procedure must never
*execute* whole-tree destructive shortcuts: `git checkout -- .`,
`git reset --hard` or `git clean` would silently discard unrelated uncommitted
work. Prose may still NAME them in a warning; the fenced code blocks — the part
a reader copies and pastes — must not contain them.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "WINDOWS_SETUP.md"


def _fenced_code(text: str) -> str:
    """Executable lines from fenced code blocks.

    Full-line comments are dropped: the repair procedure must be allowed to
    WARN about the destructive shortcuts (a `# Do NOT use ...` comment) without
    that warning counting as an executed command.
    """
    inside = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside and not line.lstrip().startswith("#"):
            lines.append(line)
    return "\n".join(lines)


def test_repair_procedure_executes_no_whole_tree_destructive_commands() -> None:
    fenced = _fenced_code(DOC.read_text(encoding="utf-8"))
    for forbidden in ("git checkout -- .", "git reset --hard", "git clean"):
        assert forbidden not in fenced, f"WINDOWS_SETUP.md executes `{forbidden}`"


def test_repair_procedure_is_scoped_and_self_verifying() -> None:
    text = DOC.read_text(encoding="utf-8")
    # Only the enumerated enforced paths are re-checked out ...
    assert "git checkout -- $paths" in text, "repair must target only the collected paths"
    # ... and the procedure compares `git status --short` before and after.
    assert text.count("git status --short") >= 2, "repair must snapshot status before and after"
