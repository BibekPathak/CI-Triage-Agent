"""Sandbox backend abstraction.

Every backend exposes a uniform interface for executing commands inside an
isolated workspace. The :class:`SandboxManager` routes to the correct backend
based on config; tools never see the backend directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SandboxConfig:
    """Configuration for a sandbox instance."""

    workspace_root: str = ""
    image: str = "ci-triage-sandbox:dev"
    cpu_limit: str = "1.0"
    memory_limit: str = "1g"
    command_timeout: float = 120.0
    network_enabled: bool = False
    pull_image: bool = True


class SandboxBackend(Protocol):
    """Uniform interface over a sandbox execution backend."""

    def start(self, config: SandboxConfig) -> str:
        """Start a sandbox; returns a sandbox_id."""
        ...

    def execute(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Execute a command inside the sandbox; returns structured output."""
        ...

    def stop(self, sandbox_id: str) -> None:
        """Stop and destroy the sandbox."""
        ...

    def copy_to(self, sandbox_id: str, host_path: str, container_path: str) -> None:
        """Copy a file/directory from host into the sandbox."""
        ...

    def copy_from(self, sandbox_id: str, container_path: str, host_path: str) -> None:
        """Copy a file/directory from the sandbox to host."""
        ...
