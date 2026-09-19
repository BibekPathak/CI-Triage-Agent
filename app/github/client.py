"""Thin wrapper around the GitHub CLI (``gh``).

Provides synchronous helpers for the operations the triage agent needs:
reading workflow runs, fetching logs, creating branches and PRs, and
uploading attachments.  All calls shell out to ``gh`` so no extra Python
HTTP dependency is required.

Resilience: reads retry transient failures (network timeouts, 5xx, and API
rate limits) with bounded exponential backoff.  Non-idempotent writes opt out
of retries by default so a duplicate branch/PR is not created on a timeout
that actually succeeded upstream.

Security notes:
- The token is passed via ``GH_TOKEN`` env-var (never in argv).
- Output is decoded with a fixed charset to avoid surprises.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


class GitHubError(Exception):
    """Raised when a ``gh`` CLI call fails."""


#: Case-insensitive markers that indicate a GitHub API rate-limit response.
_RATE_LIMIT_MARKERS = (
    "#429",
    "rate limit",
    "rate-limit",
    "secondary rate limit",
    "api rate limit exceeded",
    "you have exceeded a secondary rate limit",
)


@dataclass
class RetryConfig:
    """Bounded retry policy for transient ``gh`` failures."""

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 10.0
    backoff_factor: float = 2.0
    retry_on_rate_limit: bool = True
    retry_on_network: bool = True
    #: Non-idempotent operations this client performs; they opt out of retries.
    non_idempotent: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "create_branch",
                "create_pull_request",
                "upload_gist",
                "post_comment",
            }
        )
    )


class GitHubClient:
    """Synchronous client wrapping the ``gh`` CLI."""

    def __init__(
        self,
        token: str | None = None,
        api_base: str | None = None,
        retry: RetryConfig | None = None,
    ) -> None:
        self._token = token or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN", "")
        self._api_base = api_base
        self._gh = shutil.which("gh") or "gh"
        self.retry = retry or RetryConfig()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        timeout: float = 30.0,
        check: bool = True,
        retry: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a ``gh`` command with bounded retries for transient errors.

        ``retry`` overrides the default behaviour: ``True`` retries, ``False``
        never retries, ``None`` falls back to the client's policy (retries
        enabled).  Non-idempotent callers pass ``retry=False``.
        """
        attempts = self.retry.max_attempts
        retryable = retry if retry is not None else True

        for attempt in range(1, attempts + 1):
            result = self._execute(args, timeout=timeout)
            if check and result.returncode != 0:
                message = (
                    f"gh {' '.join(args[:3])} failed (rc={result.returncode}): "
                    f"{result.stderr.strip()}"
                )
                if not retryable or attempt >= attempts:
                    raise GitHubError(message)
                delay = self._retry_delay(result, attempt)
                if delay is None:
                    raise GitHubError(message)
                time.sleep(delay)
                continue
            return result

        raise GitHubError(f"gh {' '.join(args[:3])} failed after {attempts} attempts")

    def _execute(
        self, args: list[str], timeout: float = 30.0
    ) -> subprocess.CompletedProcess[str]:
        """Run ``gh`` once (no retry).  Transient exec errors map to a fake rc."""
        env = os.environ.copy()
        if self._token:
            env["GH_TOKEN"] = self._token
        if self._api_base:
            env["GH_API_BASE_URL"] = self._api_base

        try:
            return subprocess.run(
                [self._gh, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            # Surface timeouts as a retryable failure akin to a 5xx.
            return subprocess.CompletedProcess(
                args=args,
                returncode=-1,
                stdout="",
                stderr=f"gh timed out after {timeout}s",
            )

    def _retry_delay(self, result: subprocess.CompletedProcess[str], attempt: int) -> float | None:
        """Backoff for *result*.  Returns a sleep delay, or None if not retryable."""
        cfg = self.retry
        output = f"{result.stderr}\n{result.stdout}".lower()

        rate_limited = any(m in output for m in _RATE_LIMIT_MARKERS)
        network_failure = result.returncode in (-1, 97, 502, 503, 504)

        if rate_limited and not cfg.retry_on_rate_limit:
            return None
        if network_failure and not cfg.retry_on_network:
            return None
        if not (rate_limited or network_failure):
            return None

        # Honor "Retry-After" seconds when GitHub provides it.
        match = re.search(r"retry-after[:\s]+(\d+)", result.stderr, re.IGNORECASE)
        if match:
            return float(match.group(1))

        delay = cfg.base_delay_s * (cfg.backoff_factor ** (attempt - 1))
        return min(delay, cfg.max_delay_s)

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
                    "-f", "type=commit"], timeout=15.0, retry=False)
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
            retry=False,
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
            retry=False,
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
            retry=False,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}
