"""GitHub-backed CI context collection.

Collects the full context of a failing GitHub Actions run into a
:class:`TriageState` so the agent has everything it needs to investigate:

- repository / branch / commit SHA
- workflow name / status
- failed job / step / exit code
- CI logs (truncated to relevant sections)
- commit message / changed files / metadata

The collector is an async callable compatible with the orchestrator's
``ContextSource``: ``async (state) -> None``.  Raw logs are truncated and
summarized rather than dumped verbatim into the LLM context.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.agent.ci_parser import parse_ci_logs
from app.github.client import GitHubClient
from app.models.state import TriageState

#: Hard cap on bytes of raw logs retained on the state.
MAX_LOG_CHARS = 60_000
#: Cap on log bytes passed to the LLM (kept modest to protect context size).
MAX_LOG_PROMPT_CHARS = 8_000

#: Optional maximum changed files surfaced in the summary.
MAX_CHANGED_FILES = 50


def _truncate_logs(raw: str) -> str:
    """Truncate oversized logs while preserving the most relevant sections.

    Keeps the head (headers) and the tail (the actual error / traceback),
    dropping the middle if the log is too large.
    """
    if len(raw) <= MAX_LOG_CHARS:
        return raw

    head = raw[: MAX_LOG_CHARS // 2]
    tail = raw[-(MAX_LOG_CHARS // 2) :]
    return f"{head}\n[... log truncated ...]\n{tail}"


def _extract_branch(run: dict[str, Any]) -> str:
    for key in ("head_branch", "display_title", "head_sha"):
        if run.get(key):
            return str(run[key])
    return ""


def _extract_commit_changed_files(commit: dict[str, Any]) -> list[str]:
    files = commit.get("files") or []
    return [f.get("filename", "") for f in files if f.get("filename")]


class GitHubContextCollector:
    """Collects CI context from GitHub into a :class:`TriageState`."""

    def __init__(
        self,
        client: GitHubClient,
        max_log_chars: int = MAX_LOG_CHARS,
        max_changed_files: int = MAX_CHANGED_FILES,
    ) -> None:
        self._client = client
        self._max_log_chars = max_log_chars
        self._max_changed_files = max_changed_files

    async def collect(self, state: TriageState) -> None:
        """Fetch and populate the state with GitHub CI context."""
        repo = state.repository
        run_id = state.workflow_run_id
        if not repo or not run_id:
            return

        # Run blocking `gh` CLI calls off the event loop.
        run = await asyncio.to_thread(self._client.get_workflow_run, repo, run_id)
        raw_logs = await asyncio.to_thread(self._client.get_workflow_logs, repo, run_id)

        # 1. Populate the structured failure by parsing CI logs.
        failure = parse_ci_logs(raw_logs)
        failure.workflow = str(run.get("name") or run.get("display_title") or "")
        failure.exit_code = failure.exit_code or _run_conclusion_code(run)
        state.failure = failure

        # 2. Retain truncated raw logs on the state.
        state.ci_logs = _truncate_logs(raw_logs)

        # 3. Repository + commit context (best-effort).
        repo_meta: dict[str, Any] = {}
        commit_meta: dict[str, Any] = {}
        changed_files: list[str] = []
        head_sha = str(run.get("head_sha") or "")

        try:
            repo_meta = await asyncio.to_thread(self._client.get_repository, repo)
        except Exception:  # noqa: BLE001 - optional context
            repo_meta = {}

        if head_sha:
            try:
                commit_meta = await asyncio.to_thread(
                    self._client.get_commit, repo, head_sha
                )
                changed_files = _extract_commit_changed_files(commit_meta)
            except Exception:  # noqa: BLE001 - optional context
                commit_meta = {}

        # 4. Build a concise repository_context summary for the LLM.
        state.repository_context = self._build_context(
            repository=repo,
            run=run,
            repo_meta=repo_meta,
            commit_meta=commit_meta,
            changed_files=changed_files,
        )

    # ------------------------------------------------------------------
    def _build_context(
        self,
        repository: str,
        run: dict[str, Any],
        repo_meta: dict[str, Any],
        commit_meta: dict[str, Any],
        changed_files: list[str],
    ) -> dict[str, Any]:
        commit_msg = (commit_meta.get("commit") or {}).get("message", "")
        author = ((commit_meta.get("commit") or {}).get("author") or {}).get("name", "")
        summary = self._summary(
            repository, run, repo_meta, commit_meta, changed_files
        )

        return {
            "summary": summary,
            "repository": repository,
            "branch": _extract_branch(run),
            "commit_sha": str(run.get("head_sha") or ""),
            "commit_message": commit_msg[:500],
            "commit_author": author,
            "workflow": str(run.get("name") or run.get("display_title") or ""),
            "workflow_status": str(run.get("status") or ""),
            "workflow_conclusion": str(run.get("conclusion") or ""),
            "changed_files": changed_files[: self._max_changed_files],
            "default_branch": str(
                (repo_meta.get("defaultBranchRef") or {}).get("name", "main")
            ),
            "language": str(
                (repo_meta.get("primaryLanguage") or {}).get("name", "")
            ),
        }

    def _summary(
        self,
        repository: str,
        run: dict[str, Any],
        repo_meta: dict[str, Any],
        commit_meta: dict[str, Any],
        changed_files: list[str],
    ) -> str:
        name = (repo_meta.get("name") or "").strip() or repository
        head_branch = _extract_branch(run)
        head_sha = (run.get("head_sha") or "")[:12]
        commit_msg = (commit_meta.get("commit") or {}).get("message", "").splitlines()
        commit_first = commit_msg[0].strip() if commit_msg else ""
        files = changed_files[: self._max_changed_files]

        lines = [
            f"Repository: {name}",
            f"Branch: {head_branch or 'unknown'}",
            f"Commit: {head_sha or 'unknown'}",
        ]
        if commit_first:
            lines.append(f"Commit message: {commit_first}")
        if files:
            lines.append("Changed files:")
            lines.extend(f"  - {f}" for f in files)
        return "\n".join(lines)


def _run_conclusion_code(run: dict[str, Any]) -> int | None:
    """Map a workflow conclusion to an exit code (best-effort)."""
    conclusion = (run.get("conclusion") or "").lower()
    if conclusion in {"failure", "timed_out", "cancelled"}:
        return 1
    if conclusion == "success":
        return 0
    return None


def build_github_context_source(
    client: GitHubClient | None = None,
    max_log_chars: int = MAX_LOG_CHARS,
) -> Callable[[TriageState], Any]:
    """Return an async ``(state) -> None`` context source for the orchestrator.

    Uses a new :class:`GitHubClient` unless one is supplied.
    """
    collector = GitHubContextCollector(
        client=client or GitHubClient(), max_log_chars=max_log_chars
    )

    async def _collect(state: TriageState) -> None:
        await collector.collect(state)

    return _collect


__all__ = [
    "GitHubContextCollector",
    "build_github_context_source",
    "MAX_LOG_CHARS",
    "MAX_LOG_PROMPT_CHARS",
]
