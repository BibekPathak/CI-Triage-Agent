"""Tests for the sandbox subsystem."""

import pytest

from app.sandbox.base import SandboxConfig
from app.sandbox.local import LocalSandbox
from app.sandbox.manager import SandboxManager
from app.sandbox.runner import SandboxRunner
from app.sandbox.runners import CommandOutput


class TestLocalSandbox:
    """Test the local process sandbox."""

    def test_start_returns_sandbox_id(self):
        sandbox = LocalSandbox()
        config = SandboxConfig(workspace_root="/tmp")
        sandbox_id = sandbox.start(config)
        assert sandbox_id
        assert len(sandbox_id) == 12

    def test_execute_returns_command_output(self, tmp_path):
        sandbox = LocalSandbox()
        config = SandboxConfig(workspace_root=str(tmp_path), command_timeout=10.0)
        sandbox_id = sandbox.start(config)

        result = sandbox.execute(sandbox_id, "pwd", cwd=str(tmp_path))
        assert isinstance(result, CommandOutput)
        assert result.exit_code == 0
        assert str(tmp_path) in result.stdout

    def test_execute_respects_cwd(self, tmp_path):
        sandbox = LocalSandbox()
        config = SandboxConfig(workspace_root=str(tmp_path))
        sandbox_id = sandbox.start(config)

        result = sandbox.execute(sandbox_id, "pwd", cwd=str(tmp_path))
        assert str(tmp_path) in result.stdout

    def test_execute_unknown_sandbox_returns_error(self):
        sandbox = LocalSandbox()
        result = sandbox.execute("nonexistent", "pwd")
        assert result.exit_code == -1
        assert "not found" in (result.error or "")

    def test_stop_removes_sandbox(self):
        sandbox = LocalSandbox()
        config = SandboxConfig(workspace_root="/tmp")
        sandbox_id = sandbox.start(config)
        assert sandbox_id in sandbox._workspaces

        sandbox.stop(sandbox_id)
        assert sandbox_id not in sandbox._workspaces

    def test_stop_unknown_sandbox_noop(self):
        sandbox = LocalSandbox()
        sandbox.stop("nonexistent")  # should not raise


class TestSandboxManager:
    """Test the sandbox manager routing and lifecycle."""

    def test_start_local_backend(self, tmp_path):
        manager = SandboxManager(backend="local")
        sandbox_id = manager.start(workspace_root=str(tmp_path))
        assert sandbox_id
        assert sandbox_id in manager._instances
        manager.stop_all()

    def test_execute_local_backend(self, tmp_path):
        manager = SandboxManager(backend="local")
        sandbox_id = manager.start(workspace_root=str(tmp_path))

        result = manager.execute(sandbox_id, "echo hello", cwd=str(tmp_path))
        assert result.exit_code == 0
        assert "hello" in result.stdout
        manager.stop_all()

    def test_start_unknown_backend_raises(self):
        manager = SandboxManager(backend="unknown")
        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            manager.start()

    def test_session_context_manager(self, tmp_path):
        manager = SandboxManager(backend="local")
        with manager.session(workspace_root=str(tmp_path)) as sandbox_id:
            result = manager.execute(sandbox_id, "pwd", cwd=str(tmp_path))
            assert result.exit_code == 0
        assert sandbox_id not in manager._instances

    def test_stop_all(self, tmp_path):
        manager = SandboxManager(backend="local")
        manager.start(workspace_root=str(tmp_path))
        manager.start(workspace_root=str(tmp_path))
        assert len(manager._instances) == 2

        manager.stop_all()
        assert len(manager._instances) == 0


class TestSandboxRunner:
    """Test the runner adapter."""

    def test_runner_delegates_to_manager(self, tmp_path):
        manager = SandboxManager(backend="local")
        sandbox_id = manager.start(workspace_root=str(tmp_path))

        runner = SandboxRunner(manager, sandbox_id)
        result = runner.run("pwd", cwd=str(tmp_path))
        assert result.exit_code == 0
        assert str(tmp_path) in result.stdout
        manager.stop_all()

    def test_runner_exposes_sandbox_id(self, tmp_path):
        manager = SandboxManager(backend="local")
        sandbox_id = manager.start(workspace_root=str(tmp_path))

        runner = SandboxRunner(manager, sandbox_id)
        assert runner.sandbox_id == sandbox_id
        manager.stop_all()
