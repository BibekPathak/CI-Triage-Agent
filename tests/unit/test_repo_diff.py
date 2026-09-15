"""Tests for git diff capture and validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.repo.diff import (
    PatchOutcome,
    build_unified_diff,
    capture_diff,
    validate_unexpected_changes,
)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def git_repo(tmp_path) -> str:
    """A git repo with src/payment.py and tests/test_payment.py."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "payment.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_payment.py").write_text("def test_add():\n    pass\n")
    _git(str(repo), "add", ".")
    _git(str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-m", "initial")
    return str(repo)


class TestCaptureDiff:
    def test_empty_repo_no_changes(self, git_repo):
        result = capture_diff(git_repo)
        assert result.changed_files == []
        assert result.diff_text == ""
        assert result.nonempty is False

    def test_captures_modified_file(self, git_repo):
        Path(git_repo, "src", "payment.py").write_text("def add(a, b):\n    return a + b\n")
        result = capture_diff(git_repo)
        assert result.changed_files == ["src/payment.py"]
        assert "-" in result.diff_text and "+" in result.diff_text
        assert result.nonempty is True

    def test_captures_new_untracked_file(self, git_repo):
        Path(git_repo, "newfile.py").write_text("x = 1\n")
        result = capture_diff(git_repo)
        assert "newfile.py" in result.changed_files

    def test_not_git_repo_returns_empty(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        result = capture_diff(str(plain))
        assert result.changed_files == []
        assert result.diff_text == ""


class TestValidateUnexpectedChanges:
    def test_no_violation_for_expected_file(self):
        assert validate_unexpected_changes(["src/x.py"], ["src/x.py"]) == []

    def test_flags_unexpected_file(self):
        violations = validate_unexpected_changes(["src/x.py", "secret.py"], ["src/x.py"])
        assert violations == ["secret.py"]

    def test_allows_test_prefix(self):
        violations = validate_unexpected_changes(
            ["src/x.py", "tests/test_x.py"],
            ["src/x.py"],
            allowed_prefixes=["tests/"],
        )
        assert violations == []


class TestBuildUnifiedDiff:
    def test_marks_removed_and_added_lines(self):
        diff = build_unified_diff(
            "src/x.py",
            "def add(a, b):\n    return a - b\n",
            "def add(a, b):\n    return a + b\n",
        )
        assert "--- a/src/x.py" in diff
        assert "+++ b/src/x.py" in diff
        assert "-    return a - b" in diff
        assert "+    return a + b" in diff

    def test_heading_uses_file_path(self):
        diff = build_unified_diff("tests/test_y.py", "a", "b")
        assert "--- a/tests/test_y.py" in diff
        assert "+++ b/tests/test_y.py" in diff

    def test_unchanged_lines_prefixed_with_space(self):
        diff = build_unified_diff("src/x.py", "keep1\nkeep2\n", "keep1\nkeep3\n")
        assert " keep1" in diff
        assert "-keep2" in diff
        assert "+keep3" in diff


class TestPatchOutcome:
    def test_defaults(self):
        out = PatchOutcome(file="src/x.py", original="a", replacement="b")
        assert out.ok is True
        assert out.verified is False
        assert out.error == ""
        assert out.unified_diff == ""
