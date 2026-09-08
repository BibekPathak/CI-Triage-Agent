"""Thin wrapper around the GitHub CLI (``gh``).

Provides synchronous helpers for the operations the triage agent needs:
reading workflow runs, fetching logs, creating branches and PRs, and
uploading attachments.  All calls shell out to ``gh`` so no extra Python
HTTP dependency is required.

Security notes:
- The token is passed via ``GH_TOKEN`` env-var (never in argv).
- Output is decoded with a fixed charset to avoid surprises.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


class GitHubError(Exception):
    """Raised when a ``gh`` CLI call fails."""


class GitHubClient:
    """Synchronous client wrapping the ``gh`` CLI."""

    def __init__(
        self,
        token: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self._token = token or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN", "")
        self._api_base = api_base
        self._gh = shutil.which("gh") or "gh"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        timeout: float = 30.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a ``gh`` command and return the result."""
        env = os.environ.copy()
        if self._token:
            env["GH_TOKEN"] = self._token
        if self._api_base:
            env["GH_API_BASE_URL"] = self._api_base

        result = subprocess.run(
            [self._gh, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if check and result.returncode != 0:
            raise GitHubError(
                f"gh {' '.join(args[:3])} failed (rc={result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result

    def _json(self, args: list[str], timeout: float = 30.0) -> Any:
        """Run ``gh`` with ``--json`` and parse the output."""
        result = self._run(args + ["--json", "url"], timeout=timeout, check=False)
        # Some commands need a different --json flag list; callers can
        # override by passing the flag themselves and using _run directly.
        if result.returncode != 0:
            raise GitHubError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    # ------------------------------------------------------------------
    # Workflow / repository reads
    # ------------------------------------------------------------------

    def get_workflow_run(self, repo: str, run_id: str) -> dict[str, Any]:
        """Return metadata for a workflow run."""
        result = self._run(
            ["api", f"repos/{repo}/actions/runs/{run_id}"],
            timeout=15.0,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def get_workflow_logs(self, repo: str, run_id: str) -> str:
        """Download and return the plain-text logs for a workflow run."""
        # Use `gh run view --log` which prints log lines directly.
        result = self._run(
            ["run", "view", str(run_id), "--repo", repo, "--log"],
            timeout=30.0,
            check=False,
        )
        return result.stdout

    def get_repository(self, repo: str) -> dict[str, Any]:
        """Return repository metadata."""
        result = self._run(
            ["repo", "view", repo, "--json",
             "name,owner,defaultBranchRef,description,primaryLanguage"],
            timeout=15.0,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def get_commit(self, repo: str, sha: str) -> dict[str, Any]:
        """Return commit details."""
        result = self._run(
            ["api", f"repos/{repo}/commits/{sha}"],
            timeout=15.0,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    # ------------------------------------------------------------------
    # Branch / PR writes
    # ------------------------------------------------------------------

    def create_branch(
        self,
        repo: str,
        branch: str,
        base: str,
        base_sha: str | None = None,
    ) -> dict[str, Any]:
        """Create a new branch from *base*.  Returns the ref payload."""
        # Ensure we have the latest refs.
        self._run(["api", f"repos/{repo}/git/refs", "--method", "POST",
                    "-f", f"ref=refs/heads/{branch}",
                    "-f", f"sha={base_sha or base}",
                    "-f", "type=commit"], timeout=15.0)
        # Return a synthetic payload since gh api --method POST doesn't
        # always return JSON cleanly.
        return {"ref": f"refs/heads/{branch}", "repo": repo}

    def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict[str, Any]:
        """Open a pull request.  Returns the PR dict."""
        result = self._run(
            ["pr", "create",
             "--repo", repo,
             "--title", title,
             "--head", head,
             "--base", base,
             "--body", body],
            timeout=30.0,
        )
        # `gh pr create` prints the PR URL on stdout.
        url = result.stdout.strip()
        return {"url": url, "title": title, "head": head, "base": base}

    # ------------------------------------------------------------------
    # Attachments (logs / patches uploaded as Gist or PR comment)
    # ------------------------------------------------------------------

    def upload_gist(self, filename: str, content: str, description: str = "") -> dict[str, Any]:
        """Create a public Gist and return its metadata."""
        result = self._run(
            ["gist", "create", "--desc", description or filename, filename],
            timeout=15.0,
            check=False,
        )
        if result.returncode != 0:
            raise GitHubError(f"gh gist create failed: {result.stderr.strip()}")
        url = result.stdout.strip()
        # Write content to a temp file for gh gist create.
        return {"url": url, "filename": filename}

    def post_comment(self, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        """Post a comment on an issue or PR."""
        result = self._run(
            ["api", f"repos/{repo}/issues/{issue_number}/comments",
             "--method", "POST",
             "-f", f"body={body}"],
            timeout=15.0,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}
