"""Tests for the GitHub context collector."""

from __future__ import annotations

import pytest

from app.github.context import (
    MAX_LOG_CHARS,
    GitHubContextCollector,
    _truncate_logs,
    build_github_context_source,
)
from app.models import ErrorType, TriageState


class _FakeGitHub:
    """Mock GitHubClient exposing deterministic responses."""

    def __init__(
        self,
        run: dict | None = None,
        logs: str = "",
        repo: dict | None = None,
        commit: dict | None = None,
    ) -> None:
        self._run = run or {
            "name": "CI",
            "head_branch": "main",
            "head_sha": "abc123def456",
            "status": "completed",
            "conclusion": "failure",
        }
        self._logs = logs
        self._repo = repo or {"name": "payments", "defaultBranchRef": {"name": "main"}}
        self._commit = commit or {
            "commit": {"message": "fix refund", "author": {"name": "alice"}},
            "files": [{"filename": "src/payment.py"}, {"filename": "tests/t.py"}],
        }

    def get_workflow_run(self, repo: str, run_id: str) -> dict:
        return self._run

    def get_workflow_logs(self, repo: str, run_id: str) -> str:
        return self._logs

    def get_repository(self, repo: str) -> dict:
        return self._repo

    def get_commit(self, repo: str, sha: str) -> dict:
        return self._commit


def _sample_logs() -> str:
    return (
        "##[group]Job: test\n"
        "##[group]Run python -m pytest\n"
        "============================= test session starts ==============================\n"
        "tests/test_payment.py:142: in test_refund\n"
        "    assert refund_amount == expected\n"
        "E   AssertionError: expected 200, got 500\n"
        "FAILED tests/test_payment.py::test_refund\n"
        "\n"
        "The process '/usr/bin/python' failed with exit code 1\n"
    )


class TestTruncateLogs:
    def test_short_logs_unchanged(self):
        raw = "short log"
        assert _truncate_logs(raw) == raw

    def test_long_logs_preserve_head_and_tail(self):
        raw = "HEAD\n" + ("x" * (MAX_LOG_CHARS + 100)) + "\nTAIL"
        out = _truncate_logs(raw)
        # Output should be bounded near MAX_LOG_CHARS + marker, far smaller
        # than the ~60KB+ input.
        assert len(out) <= MAX_LOG_CHARS + len("[... log truncated ...]") + 10
        assert "HEAD" in out
        assert "TAIL" in out
        assert "... log truncated ..." in out


class TestCollect:
    @pytest.mark.asyncio
    async def test_populates_failure_from_logs(self):
        client = _FakeGitHub(logs=_sample_logs())
        collector = GitHubContextCollector(client)  # type: ignore[arg-type]

        state = TriageState(repository="acme/payments", workflow_run_id="42")
        await collector.collect(state)

        assert state.failure.workflow == "CI"
        assert state.failure.file == "tests/test_payment.py"
        assert state.failure.test_name == "test_refund"
        assert state.failure.line == 142
        assert state.failure.exit_code == 1
        assert state.failure.error_type == ErrorType.DETERMINISTIC_CODE
        assert state.ci_logs == _sample_logs()

    @pytest.mark.asyncio
    async def test_populates_repository_context(self):
        client = _FakeGitHub(logs=_sample_logs())
        collector = GitHubContextCollector(client)  # type: ignore[arg-type]

        state = TriageState(repository="acme/payments", workflow_run_id="42")
        await collector.collect(state)

        ctx = state.repository_context
        assert "fix refund" in ctx["summary"]
        assert "src/payment.py" in ctx["changed_files"]
        assert ctx["commit_sha"] == "abc123def456"
        assert ctx["branch"] == "main"
        assert ctx["workflow_conclusion"] == "failure"

    @pytest.mark.asyncio
    async def test_maps_conclusion_to_exit_code(self):
        run = {"name": "CI", "status": "completed", "conclusion": "failure"}
        client = _FakeGitHub(run=run, logs="no exit code here")
        collector = GitHubContextCollector(client)  # type: ignore[arg-type]

        state = TriageState(repository="acme/payments", workflow_run_id="42")
        await collector.collect(state)
        assert state.failure.exit_code == 1

    @pytest.mark.asyncio
    async def test_handles_missing_context_gracefully(self):
        client = _FakeGitHub(repo={}, commit={})
        collector = GitHubContextCollector(client)  # type: ignore[arg-type]

        state = TriageState(repository="acme/payments", workflow_run_id="42")
        await collector.collect(state)
        assert state.repository_context != {}


class TestBuildContextSource:
    @pytest.mark.asyncio
    async def test_factory_returns_async_callable(self):
        client = _FakeGitHub(logs=_sample_logs())
        source = build_github_context_source(client=client)  # type: ignore[arg-type]
        assert callable(source)

        state = TriageState(repository="acme/payments", workflow_run_id="42")
        await source(state)
        assert state.ci_logs
        assert state.failure.error_type == ErrorType.DETERMINISTIC_CODE
