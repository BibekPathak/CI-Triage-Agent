"""GitHub integration (CLI-based client + context collection)."""

from app.github.client import GitHubClient, GitHubError
from app.github.context import GitHubContextCollector, build_github_context_source

__all__ = [
    "GitHubClient",
    "GitHubContextCollector",
    "GitHubError",
    "build_github_context_source",
]
