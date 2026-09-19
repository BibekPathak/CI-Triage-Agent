"""Tests for GitHub client resilience: retries, backoff, rate-limit handling."""

from __future__ import annotations

import subprocess

import pytest

from app.github.client import GitHubClient, GitHubError, RetryConfig


def _proc(rc: int, stderr: str = "", stdout: str = "{}") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


class TestRetryConfigDefaults:
    def test_default_policy(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.retry_on_rate_limit is True
        assert cfg.retry_on_network is True
        assert "create_pull_request" in cfg.non_idempotent
        assert "get_workflow_run" not in cfg.non_idempotent


class TestRetryBehaviour:
    def _client(self, monkeypatch, results=None) -> tuple[GitHubClient, list, list]:
        client = GitHubClient(retry=RetryConfig(base_delay_s=0.01, max_delay_s=0.02))
        calls: list = []
        sequence = list(results or [])

        def fake_execute(args, timeout=30.0):
            calls.append(args)
            if len(calls) > len(sequence):
                return _proc(0)
            return sequence[len(calls) - 1]

        monkeypatch.setattr(client, "_execute", fake_execute)
        return client, calls, sequence

    def test_success_first_attempt_no_retry(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [_proc(0)])
        client._run(["run", "view"], check=True)
        assert calls and len(calls) == 1

    def test_rate_limit_then_success(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [
            _proc(1, stderr="You have exceeded a secondary rate limit"),
            _proc(0),
        ])
        out = client._run(["api", "repos/x/runs/1"], check=True)
        assert out.returncode == 0
        assert len(calls) == 2

    def test_rate_limit_exhausted_raises(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [
            _proc(1, stderr="API rate limit exceeded"),
            _proc(1, stderr="API rate limit exceeded"),
            _proc(1, stderr="API rate limit exceeded"),
        ])
        with pytest.raises(GitHubError, match="rate limit"):
            client._run(["api", "repos/x/runs/1"], check=True)
        assert len(calls) == 3

    def test_network_failure_retries(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [
            _proc(502, stderr="bad gateway"),
            _proc(0),
        ])
        out = client._run(["api", "repos/x/runs/1"], check=True)
        assert out.returncode == 0
        assert len(calls) == 2

    def test_honors_retry_after(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [
            _proc(1, stderr="message: retry-after: 4\nYou have exceeded a secondary rate limit"),
            _proc(0),
        ])
        # base delay is tiny, but Retry-After (4) should dominate.
        client._run(["api", "repos/x/runs/1"], check=True)
        assert len(calls) == 2

    def test_non_retryable_error_raises_immediately(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [_proc(1, stderr="not found")])
        with pytest.raises(GitHubError, match="not found"):
            client._run(["api", "repos/x/commits/abc"], check=True)
        assert len(calls) == 1

    def test_retry_false_disables_retries(self, monkeypatch):
        client, calls, _ = self._client(monkeypatch, [
            _proc(502, stderr="bad gateway"),
            _proc(502, stderr="bad gateway"),
            _proc(0),
        ])
        with pytest.raises(GitHubError):
            client._run(["api", "repos/x"], check=True, retry=False)
        assert len(calls) == 1

    def test_execute_converts_timeout_to_retryable_rc(self, monkeypatch):
        # _execute swallows subprocess.TimeoutExpired into an rc=-1 result so
        # the retry loop (which sees only result objects) can retry it.
        import subprocess as sp

        client = GitHubClient()

        def boom(args, capture_output=True, text=True, timeout=None, env=None):
            raise sp.TimeoutExpired("gh", timeout or 10)

        monkeypatch.setattr(sp, "run", boom)
        result = client._execute(["api", "repos/x/runs/1"], timeout=10.0)
        assert result.returncode == -1
        assert "timed out" in result.stderr

    def test_timeout_result_is_retryable(self, monkeypatch):
        # An rc=-1 (timeout) result retries, then succeeds on the second try.
        client, calls, _ = self._client(monkeypatch, [
            _proc(-1, stderr="gh timed out after 10s"),
            _proc(0),
        ])
        out = client._run(["api", "repos/x/runs/1"], check=True)
        assert out.returncode == 0
        assert len(calls) == 2

    def test_check_false_returns_result_without_retry(self, monkeypatch):
        # check=False means non-zero is not treated as a hard failure; the
        # original behavior returns the result unchanged.
        client, calls, _ = self._client(monkeypatch, [_proc(1, stderr="whatever")])
        out = client._run(["api", "repos/x"], check=False)
        assert out.returncode == 1
        assert len(calls) == 1


class TestWriteOptOut:
    """Non-idempotent writes must not retry on a rate limit / failure."""

    def test_create_pull_request_does_not_retry(self, monkeypatch):
        client = GitHubClient(retry=RetryConfig(base_delay_s=0.01))
        calls: list = []

        def fake_execute(args, timeout=30.0):
            calls.append(args)
            return _proc(1, stderr="API rate limit exceeded")

        monkeypatch.setattr(client, "_execute", fake_execute)
        with pytest.raises(GitHubError, match="rate limit"):
            client.create_pull_request("owner/repo", "t", "head", "main", "b")
        assert len(calls) == 1

    def test_read_retries_but_write_does_not(self, monkeypatch):
        client = GitHubClient(retry=RetryConfig(base_delay_s=0.01))
        calls: list = []

        def fake_execute(args, timeout=30.0):
            calls.append(args)
            return _proc(1, stderr="API rate limit exceeded")

        monkeypatch.setattr(client, "_execute", fake_execute)
        # Read: retried up to max_attempts.
        with pytest.raises(GitHubError):
            client.get_workflow_run("owner/repo", "1")
        assert len(calls) == client.retry.max_attempts
