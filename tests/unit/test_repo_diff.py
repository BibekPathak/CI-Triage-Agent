"""Tests for git diff capture and validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.repo.diff import (
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
