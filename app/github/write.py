"""GitHub write path: branch, commit, push, and pull request.

Runs ONLY after a human has approved a triage fix.  The approval boundary is
enforced here: if the state is not APPROVED or the patch is not signed off,
no remote write is performed.

Security:
- Credentials passed via env / GIT_ASKPASS only, never in argv or logs.
- No ``shell=True`` anywhere.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from dataclasses import dataclass

from app.github.client import GitHubClient
from app.models.domain import ApprovalStatus
from app.models.state import TriageState
from app.repo.checkout import RepoCheckoutError, _write_askpass_script


class ApprovalRequiredError(Exception):
    """Raised when a remote write is attempted before approval."""


@dataclass
class PRResult:
    """Outcome of the GitHub write path."""

    branch: str
    base: str
    pr_url: str = ""
    pushed: bool = False
    commit_sha: str = ""


def _run_git(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run git without a shell; sanitize credentials in errors."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env or os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise RepoCheckoutError("git push timed out") from exc
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise RepoCheckoutError("git executable not found") from exc
    if result.returncode != 0:
        err = _sanitize(result.stderr.strip())
        raise RepoCheckoutError(f"git {args[0]} failed: {err}")
    return result


def _sanitize(message: str) -> str:
    """Redact credentials from any surfaced error message."""
    message = re.sub(r"(://[^:/@\s]+):[^@\s]*@", r"\1:***@", message)
    tokens = (
        "x-access-token",
        os.environ.get("GH_TOKEN", ""),
        os.environ.get("GITHUB_TOKEN", ""),
    )
    for tok in tokens:
        if tok:
            message = message.replace(tok, "***")
    return message[:500]


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def commit_and_push(
    workspace_root: str,
    branch: str,
    *,
    message: str,
    remote: str = "origin",
) -> str:
    """Commit the working-tree change in *workspace_root* and push *branch*.

    Returns the pushed commit SHA.  Assumes the workspace is a git checkout
    with the default-branch remote configured (from repository checkout).
    """
    env = _git_env()
    askpass: str | None = None
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        askpass = _write_askpass_script()
        env["GIT_ASKPASS"] = askpass

    try:
        # Ensure we are on a detached/default state, then create the branch.
        _run_git(["checkout", "-B", branch], cwd=workspace_root, env=env)
        _run_git(["add", "-A"], cwd=workspace_root, env=env)
        _run_git(
            ["commit", "-m", message],
            cwd=workspace_root,
            env=env,
            timeout=60.0,
        )
        _run_git(["push", "-u", remote, branch], cwd=workspace_root, env=env)
    finally:
        if askpass:
            with contextlib.suppress(OSError):
                os.unlink(askpass)

    result = _run_git(["rev-parse", "HEAD"], cwd=workspace_root, env=env)
    return result.stdout.strip()


def open_fix_pr(
    repository: str,
    state: TriageState,
    workspace_root: str = "",
    *,
    client: GitHubClient | None = None,
    base_branch: str = "main",
    token: str | None = None,
) -> PRResult:
    """Create a branch, commit, push, and open a PR for an approved fix.

    Raises :class:`ApprovalRequiredError` unless the fix is approved and the
    patch is signed off.  The fix is reconstructed from the persisted state
    (``candidate_patch``), so a previously cleaned-up workspace is fine.
    """
    if state.approval_status != ApprovalStatus.APPROVED:
        raise ApprovalRequiredError(
            "cannot open a PR: fix has not been approved"
        )
    if not state.diff_signed_off:
        raise ApprovalRequiredError(
            "cannot open a PR: patch is not signed off"
        )
    if not state.candidate_patch:
        raise ApprovalRequiredError(
            "cannot open a PR: no candidate patch was captured"
        )

    client = client or GitHubClient()
    base = state.repository_context.get("default_branch") or base_branch
    branch = f"fix/{state.triage_id[:8]}"

    # Reconstruct a fresh checkout if no live workspace is provided.
    owns_workspace = not workspace_root
    if owns_workspace:
        from app.repo import prepare_workspace

        workspace = prepare_workspace(repository, sha=None, token=token)
        workspace_root = workspace.root

    try:
        client.create_branch(repository, branch, base)
        _apply_candidate_patch(workspace_root, state.candidate_patch)
        commit_sha = commit_and_push(
            workspace_root,
            branch,
            message=state.proposed_pr_title
            or f"fix: resolve failing {state.failure.test_name or 'test'}",
        )

        pr = client.create_pull_request(
            repository,
            title=state.proposed_pr_title or "fix: resolve CI failure",
            head=branch,
            base=base,
            body=state.proposed_pr_body or "",
        )
    finally:
        if owns_workspace:
            workspace.cleanup()

    return PRResult(
        branch=branch,
        base=base,
        pr_url=str(pr.get("url", "")),
        pushed=True,
        commit_sha=commit_sha,
    )


def _apply_candidate_patch(workspace_root: str, candidate_patch: str) -> None:
    """Apply the captured unified diff to the working tree.

    Falls back to ``git apply`` (patch is already a unified diff from
    Milestone 6's capture).
    """
    env = _git_env()
    patch_path = os.path.join(workspace_root, ".cta_patch.diff")
    with open(patch_path, "w", encoding="utf-8") as fh:
        fh.write(candidate_patch)
    try:
        _run_git(["apply", "--check", ".cta_patch.diff"], cwd=workspace_root, env=env)
        _run_git(["apply", ".cta_patch.diff"], cwd=workspace_root, env=env)
    finally:
        os.remove(patch_path)


__all__ = [
    "ApprovalRequiredError",
    "PRResult",
    "commit_and_push",
    "open_fix_pr",
]
