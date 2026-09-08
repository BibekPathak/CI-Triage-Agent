"""Local process sandbox.

Wraps the existing :class:`LocalCommandRunner` behind the sandbox interface so
the orchestrator and executor can treat local and Docker execution uniformly.

This backend is used by:
- Unit/integration tests (no Docker dependency)
- The deterministic demo mode (`make demo`)
- Development when Docker is unavailable
"""

from __future__ import annotations

import uuid

from app.sandbox.base import SandboxConfig
from app.sandbox.runners import CommandOutput, LocalCommandRunner


class LocalSandbox:
    """Local process sandbox wrapping the policy-enforced command runner."""

    def __init__(self) -> None:
        self._runner = LocalCommandRunner()
        self._workspaces: dict[str, dict] = {}

    def start(self, config: SandboxConfig) -> str:
        """Create a local sandbox (just records the workspace path)."""
        sandbox_id = uuid.uuid4().hex[:12]
        self._workspaces[sandbox_id] = {
            "config": config,
            "workspace_root": config.workspace_root,
        }
        return sandbox_id

    def execute(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandOutput:
        """Execute a command locally via the policy-enforced runner."""
        if sandbox_id not in self._workspaces:
            return CommandOutput(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=0,
                error=f"Sandbox {sandbox_id} not found",
            )

        workspace = self._workspaces[sandbox_id]["workspace_root"]
        effective_cwd = cwd or workspace or None
        effective_timeout = timeout or self._workspaces[sandbox_id]["config"].command_timeout

        return self._runner.run(command, timeout=effective_timeout, cwd=effective_cwd)

    def stop(self, sandbox_id: str) -> None:
        """Stop a local sandbox (no-op; local processes are not tracked)."""
        self._workspaces.pop(sandbox_id, None)

    def copy_to(self, sandbox_id: str, host_path: str, container_path: str) -> None:
        """Copy to local workspace (no-op; files are already on host)."""
        pass

    def copy_from(self, sandbox_id: str, container_path: str, host_path: str) -> None:
        """Copy from local workspace (no-op; files are already on host)."""
        pass
