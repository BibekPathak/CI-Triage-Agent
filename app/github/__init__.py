"""GitHub integration (CLI-based client + context collection)."""

from app.github.client import GitHubClient, GitHubError
from app.github.context import GitHubContextCollector, build_github_context_source
from app.github.write import (
    ApprovalRequiredError,
    PRResult,
    commit_and_push,
    open_fix_pr,
)

__all__ = [
    "ApprovalRequiredError",
    "GitHubClient",
    "GitHubContextCollector",
    "GitHubError",
    "PRResult",
    "build_github_context_source",
    "commit_and_push",
    "open_fix_pr",
]
