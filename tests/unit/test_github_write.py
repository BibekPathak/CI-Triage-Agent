"""Tests for the GitHub write path (branch/commit/push/PR)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.github.write import (
    ApprovalRequiredError,
    commit_and_push,
    open_fix_pr,
)
from app.models.domain import ApprovalStatus
from app.models.state import TriageState
from app.repo.checkout import RepoCheckoutError


def _git(repo: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture()
def bare_remote(tmp_path) -> str:
    """A bare git remote (origin) with one commit on main."""
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "origin.git", check=False)
    return str(remote)


@pytest.fixture()
def working_repo(tmp_path, bare_remote) -> str:
    """A local clone of bare_remote with src/payment.py committed."""
    _git(tmp_path, "clone", bare_remote, "work", check=False)
    repo = tmp_path / "work"
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "payment.py").write_text("def add(a, b):\n    return a - b\n")
    _git(str(repo), "add", ".")
    _git(str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-m", "initial")
    _git(str(repo), "branch", "-M", "main")
    _git(str(repo), "push", "-u", "origin", "main")
    return str(repo)


def _approved_state(repo_name: str = "acme/payments") -> TriageState:
    state = TriageState(
        repository=repo_name,
        workflow_run_id="42",
        approval_status=ApprovalStatus.APPROVED,
        diff_signed_off=True,
        changed_files=["src/payment.py"],
    )
    state.proposed_pr_title = "fix: payment add bug"
    state.proposed_pr_body = "## Summary\nResolve sign bug."
    state.candidate_patch = (
        "--- a/src/payment.py\n+++ b/src/payment.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    return state


class TestOpenFixPrGates:
    def test_raises_when_not_approved(self):
        state = _approved_state()
        state.approval_status = ApprovalStatus.PENDING
        with pytest.raises(ApprovalRequiredError):
            open_fix_pr(state.repository, state, workspace_root="/tmp")

    def test_raises_when_not_signed_off(self):
        state = _approved_state()
        state.diff_signed_off = False
        with pytest.raises(ApprovalRequiredError):
            open_fix_pr(state.repository, state, workspace_root="/tmp")

    def test_raises_when_no_patch(self):
        state = _approved_state()
        state.candidate_patch = None
        with pytest.raises(ApprovalRequiredError):
            open_fix_pr(state.repository, state, workspace_root="/tmp")


class TestOpenFixPrHappyPath:
    def test_opens_pr_with_mocked_client(self, working_repo, tmp_path):
        client = MagicMock()
        client.create_branch.return_value = {"ref": "refs/heads/fix/abc12345"}
        client.create_pull_request.return_value = {
            "url": "https://github.com/acme/payments/pull/9"
        }

        state = _approved_state()
        # Use the working repo directly as the reconstruction workspace.
        result = open_fix_pr(
            "acme/payments",
            state,
            workspace_root=working_repo,
            client=client,
        )

        assert result.pushed is True
        assert result.branch.startswith("fix/")
        assert result.pr_url == "https://github.com/acme/payments/pull/9"
        client.create_branch.assert_called_once_with("acme/payments", result.branch, "main")
        client.create_pull_request.assert_called_once()
        # The patch should have been applied to the working tree target.
        applied = Path(working_repo, "src", "payment.py").read_text()
        assert "return a + b" in applied


class TestCommitAndPush:
    def test_commits_and_pushes_to_remote(self, working_repo, bare_remote):
        # Apply a change and commit+push to a branch, then verify it's on origin.
        Path(working_repo, "src", "payment.py").write_text(
            "def add(a, b):\n    return a + b\n"
        )
        sha = commit_and_push(
            working_repo,
            "fix/abc",
            message="fix: bug",
        )
        assert sha

        # Verify the branch exists on the bare remote.
        branches = _git(
            bare_remote, "for-each-ref", "refs/heads/fix/abc", check=False
        )
        assert branches.stdout.strip()

    def test_commit_requires_change(self, working_repo):
        # No change to commit should raise.
        with pytest.raises(RepoCheckoutError):
            commit_and_push(working_repo, "fix/empty", message="nothing")
