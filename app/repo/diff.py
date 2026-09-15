"""Git diff capture and validation over a working tree.

Captures the real working-tree diff of a checked-out repository so the agent
can produce inspectable, reviewable patches and detect when it modified
files it was not expected to touch.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.repo.checkout import RepoCheckoutError


@dataclass
class DiffResult:
    """Captured working-tree changes."""

    changed_files: list[str] = field(default_factory=list)
    diff_text: str = ""
    stat_text: str = ""
    nonempty: bool = False


def _git(root: str, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run a git command scoped to *root* without a shell."""
    try:
        return subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise RepoCheckoutError("git diff timed out") from exc
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise RepoCheckoutError("git executable not found") from exc


def _status_lines(root: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain`` into (XY, path) tuples.

    Handles renames (``R old -> new``) and quoting for paths with spaces.
    """
    result = _git(
        root,
        "-c", "core.quotepath=false",
        "status", "--porcelain", "--untracked-files=all",
    )
    lines: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        rest = line[3:].strip()
        if xy.startswith("R") or xy.startswith("C"):
            # rename/copy:  "R  old" then "->" is dropped in --porcelain
            path = rest
            lines.append((xy, path))
        else:
            lines.append((xy, rest))
    return lines


def capture_diff(workspace_root: str) -> DiffResult:
    """Capture the working-tree diff for *workspace_root*.

    Uses intent-to-add for untracked files so a newly created file appears in
    the returned patch.  The index is not staged (no content is committed).
    """
    root = workspace_root or ""
    result = DiffResult()

    if not root or not (Path(root) / ".git").exists():
        return result

    status = _status_lines(root)
    changed: list[str] = []
    untracked: list[str] = []
    for xy, path in status:
        if xy[1] == "?":
            untracked.append(path)
        else:
            changed.append(path)
    changed = _dedupe(changed + untracked)

    # Mark untracked files as intent-to-add so `git diff` includes them.
    for path in untracked:
        _git(root, "add", "-N", "--", path)

    diff = _git(root, "diff")
    stat = _git(root, "diff", "--stat")

    result.changed_files = changed
    result.diff_text = diff.stdout
    result.stat_text = stat.stdout
    result.nonempty = bool(changed)
    return result


def validate_unexpected_changes(
    changed_files: list[str],
    expected_files: list[str],
    allowed_prefixes: list[str] | None = None,
) -> list[str]:
    """Return files changed by the agent that were not expected.

    *expected_files* are the target files the patch was supposed to modify.
    *allowed_prefixes* optionally permit changes under certain paths (e.g.
    tests).  Files not matching are returned as a list of violations.
    """
    allowed_prefixes = allowed_prefixes or []
    expected = {e.lstrip("/") for e in expected_files}
    violations: list[str] = []
    for f in changed_files:
        f = f.lstrip("/")
        if f in expected:
            continue
        if any(f.startswith(p) for p in allowed_prefixes):
            continue
        violations.append(f)
    return violations


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen


__all__ = ["DiffResult", "capture_diff", "validate_unexpected_changes"]
