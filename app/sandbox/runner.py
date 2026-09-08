"""Sandbox-backed command runner.

Wraps :class:`SandboxManager` behind the same ``run()`` interface expected by
the shell and test tools. The orchestrator injects this runner into the
executor, which distributes it to all tools that need to execute commands.

Usage::

    manager = SandboxManager()
    runner = SandboxRunner(manager, sandbox_id="abc123")
    executor.set_runner(runner)
"""

from __future__ import annotations

from app.sandbox.manager import SandboxManager
from app.sandbox.runners import CommandOutput


class SandboxRunner:
    """Runner that delegates to a SandboxManager-managed sandbox."""

    def __init__(self, manager: SandboxManager, sandbox_id: str) -> None:
        self._manager = manager
        self._sandbox_id = sandbox_id

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandOutput:
        """Execute a command inside the managed sandbox."""
        return self._manager.execute(
            self._sandbox_id,
            command,
            cwd=cwd,
            timeout=timeout,
        )

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id
