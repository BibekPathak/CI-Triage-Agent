"""Sandbox manager: lifecycle + backend routing.

The :class:`SandboxManager` creates, manages, and destroys sandbox instances
for triage runs. It routes to the correct backend (Docker or local) based on
the ``SANDBOX_BACKEND`` configuration and provides a uniform interface for the
orchestrator and executor.

Usage::

    manager = SandboxManager()
    sandbox_id = manager.start(workspace_root="/path/to/repo")
    result = manager.execute(sandbox_id, "pytest -q")
    manager.stop(sandbox_id)

The manager ensures cleanup even on abnormal termination.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from app.config import settings
from app.sandbox.base import SandboxConfig
from app.sandbox.docker import DockerSandbox
from app.sandbox.local import LocalSandbox
from app.sandbox.runners import CommandOutput


class SandboxManager:
    """Creates, manages, and destroys sandbox instances."""

    def __init__(self, backend: str | None = None) -> None:
        self._backend_name = backend or settings.sandbox_backend
        self._backends = {
            "docker": DockerSandbox,
            "local": LocalSandbox,
        }
        self._instances: dict[str, dict] = {}

    def _get_backend(self, backend_name: str):
        """Instantiate the requested backend."""
        cls = self._backends.get(backend_name)
        if cls is None:
            raise ValueError(
                f"Unknown sandbox backend: {backend_name!r}. "
                f"Available: {list(self._backends.keys())}"
            )
        return cls()

    def start(
        self,
        workspace_root: str = "",
        *,
        backend: str | None = None,
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
        network_enabled: bool | None = None,
        command_timeout: float | None = None,
    ) -> str:
        """Start a new sandbox instance.

        Returns a sandbox_id for use with execute/stop.
        """
        resolved_backend = backend or self._backend_name
        config = SandboxConfig(
            workspace_root=workspace_root,
            image=settings.sandbox_image,
            cpu_limit=cpu_limit or settings.sandbox_cpu_limit,
            memory_limit=memory_limit or settings.sandbox_memory_limit,
            command_timeout=command_timeout or settings.sandbox_command_timeout,
            network_enabled=(
                network_enabled
                if network_enabled is not None
                else settings.sandbox_network
            ),
            pull_image=settings.sandbox_pull,
        )

        backend_instance = self._get_backend(resolved_backend)
        sandbox_id = backend_instance.start(config)

        self._instances[sandbox_id] = {
            "backend": backend_instance,
            "config": config,
            "backend_name": resolved_backend,
        }
        return sandbox_id

    def execute(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandOutput:
        """Execute a command inside the sandbox."""
        if sandbox_id not in self._instances:
            return CommandOutput(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=0,
                error=f"Sandbox {sandbox_id} not found",
            )

        instance = self._instances[sandbox_id]
        return instance["backend"].execute(sandbox_id, command, cwd=cwd, timeout=timeout)

    def stop(self, sandbox_id: str) -> None:
        """Stop and destroy a sandbox instance."""
        instance = self._instances.pop(sandbox_id, None)
        if instance is not None:
            instance["backend"].stop(sandbox_id)

    def stop_all(self) -> None:
        """Stop all running sandbox instances."""
        for sandbox_id in list(self._instances.keys()):
            self.stop(sandbox_id)

    def copy_to(self, sandbox_id: str, host_path: str, container_path: str) -> None:
        """Copy a file/directory from host into the sandbox."""
        if sandbox_id not in self._instances:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")
        self._instances[sandbox_id]["backend"].copy_to(
            sandbox_id, host_path, container_path
        )

    def copy_from(self, sandbox_id: str, container_path: str, host_path: str) -> None:
        """Copy a file/directory from the sandbox to host."""
        if sandbox_id not in self._instances:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")
        self._instances[sandbox_id]["backend"].copy_from(
            sandbox_id, container_path, host_path
        )

    @contextmanager
    def session(
        self,
        workspace_root: str = "",
        **kwargs,
    ) -> Generator[str, None, None]:
        """Context manager that ensures sandbox cleanup."""
        sandbox_id = self.start(workspace_root=workspace_root, **kwargs)
        try:
            yield sandbox_id
        finally:
            self.stop(sandbox_id)
