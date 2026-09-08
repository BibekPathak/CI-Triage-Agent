"""GitHub integration (CLI-based client + tool wrappers)."""

from app.github.client import GitHubClient, GitHubError

__all__ = ["GitHubClient", "GitHubError"]
