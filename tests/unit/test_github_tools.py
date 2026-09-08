"""Tests for GitHub tools (mocked client)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.github.client import GitHubClient, GitHubError
from app.tools.github import (
    CreateBranchTool,
    CreatePullRequestTool,
    GetCommitTool,
    GetRepositoryTool,
    GetWorkflowLogsTool,
    GetWorkflowRunTool,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _mock_client(**methods: Any) -> GitHubClient:
    """Return a GitHubClient with mocked methods."""
    client = MagicMock(spec=GitHubClient)
    for name, val in methods.items():
        mock_method = MagicMock()
        if isinstance(val, MagicMock) and val.side_effect is not MagicMock:
            mock_method.side_effect = val.side_effect
        else:
            mock_method.return_value = val
        setattr(client, name, mock_method)
    return client


# ------------------------------------------------------------------
# GetWorkflowRunTool
# ------------------------------------------------------------------

class TestGetWorkflowRunTool:
    def test_ok(self):
        expected = {"id": 123, "status": "completed", "conclusion": "success"}
        client = _mock_client(get_workflow_run=expected)
        tool = GetWorkflowRunTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "workflow_run_id": "456"})
        assert result.ok
        assert result.data == expected

    def test_error(self):
        client = _mock_client(get_workflow_run=MagicMock(side_effect=GitHubError("not found")))
        tool = GetWorkflowRunTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "workflow_run_id": "999"})
        assert not result.ok
        assert "not found" in (result.error or "")


# ------------------------------------------------------------------
# GetWorkflowLogsTool
# ------------------------------------------------------------------

class TestGetWorkflowLogsTool:
    def test_ok(self):
        client = _mock_client(get_workflow_logs="line1\nline2\n")
        tool = GetWorkflowLogsTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "workflow_run_id": "1"})
        assert result.ok
        assert "line1" in result.stdout

    def test_error(self):
        client = _mock_client(get_workflow_logs=MagicMock(side_effect=GitHubError("no logs")))
        tool = GetWorkflowLogsTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "workflow_run_id": "1"})
        assert not result.ok


# ------------------------------------------------------------------
# GetRepositoryTool
# ------------------------------------------------------------------

class TestGetRepositoryTool:
    def test_ok(self):
        expected = {"name": "repo", "defaultBranchRef": "main"}
        client = _mock_client(get_repository=expected)
        tool = GetRepositoryTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo"})
        assert result.ok
        assert result.data == expected

    def test_error(self):
        client = _mock_client(get_repository=MagicMock(side_effect=GitHubError("404")))
        tool = GetRepositoryTool()
        tool._client = client

        result = tool.run({"repository": "owner/missing"})
        assert not result.ok


# ------------------------------------------------------------------
# GetCommitTool
# ------------------------------------------------------------------

class TestGetCommitTool:
    def test_ok(self):
        expected = {"sha": "abc123", "message": "fix: bug"}
        client = _mock_client(get_commit=expected)
        tool = GetCommitTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "sha": "abc123"})
        assert result.ok
        assert result.data == expected

    def test_error(self):
        client = _mock_client(get_commit=MagicMock(side_effect=GitHubError("not found")))
        tool = GetCommitTool()
        tool._client = client

        result = tool.run({"repository": "owner/repo", "sha": "bad"})
        assert not result.ok


# ------------------------------------------------------------------
# CreateBranchTool
# ------------------------------------------------------------------

class TestCreateBranchTool:
    def test_ok(self):
        expected = {"ref": "refs/heads/fix-1", "repo": "owner/repo"}
        client = _mock_client(create_branch=expected)
        tool = CreateBranchTool()
        tool._client = client

        result = tool.run({
            "repository": "owner/repo",
            "branch": "fix-1",
            "base_branch": "main",
        })
        assert result.ok
        assert result.data == expected

    def test_error(self):
        client = _mock_client(create_branch=MagicMock(side_effect=GitHubError("ref exists")))
        tool = CreateBranchTool()
        tool._client = client

        result = tool.run({
            "repository": "owner/repo",
            "branch": "fix-1",
            "base_branch": "main",
        })
        assert not result.ok


# ------------------------------------------------------------------
# CreatePullRequestTool
# ------------------------------------------------------------------

class TestCreatePullRequestTool:
    def test_ok(self):
        expected = {"url": "https://github.com/owner/repo/pull/42", "title": "Fix"}
        client = _mock_client(create_pull_request=expected)
        tool = CreatePullRequestTool()
        tool._client = client

        result = tool.run({
            "repository": "owner/repo",
            "title": "Fix",
            "head": "fix-1",
            "base": "main",
            "body": "Fixes #1",
        })
        assert result.ok
        assert result.data == expected

    def test_error(self):
        client = _mock_client(create_pull_request=MagicMock(side_effect=GitHubError("conflict")))
        tool = CreatePullRequestTool()
        tool._client = client

        result = tool.run({
            "repository": "owner/repo",
            "title": "Fix",
            "head": "fix-1",
            "base": "main",
        })
        assert not result.ok


# ------------------------------------------------------------------
# Registry surface
# ------------------------------------------------------------------

class TestRegistrySurface:
    def test_all_github_tools_registered(self):
        from app.tools.registry import default_registry
        reg = default_registry()
        names = reg.names()
        for name in [
            "get_workflow_run",
            "get_workflow_logs",
            "get_repository",
            "get_commit",
            "create_branch",
            "create_pull_request",
        ]:
            assert name in names, f"{name} missing from registry"
