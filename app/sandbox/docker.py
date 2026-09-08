"""Docker-backed sandbox.

Runs commands inside isolated Docker containers with CPU/memory limits,
network control, execution timeouts, and automatic teardown. The container is
created on demand and destroyed when the triage run ends.

Security properties:
- No host Docker socket mounted.
- No host credentials leaked.
- CPU/memory limits enforced.
- Network disabled by default.
- Read-only root filesystem where practical (workspace mounted rw).
"""

from __future__ import annotations

import contextlib
import subprocess
import time
import uuid

from app.sandbox.base import SandboxConfig
from app.sandbox.runners import CommandOutput, _decode


def _docker(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run a docker CLI command and return the result."""
    cmd = ["docker", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class DockerSandbox:
    """Docker-backed sandbox with full lifecycle management."""

    def __init__(self) -> None:
        self._containers: dict[str, dict] = {}

    def start(self, config: SandboxConfig) -> str:
        """Create and start a Docker container for the sandbox.

        Returns a sandbox_id that can be used for execute/stop.
        """
        sandbox_id = uuid.uuid4().hex[:12]
        container_name = f"cta-{sandbox_id}"

        # Pull image if needed.
        if config.pull_image:
            _docker("pull", config.image, timeout=120.0)

        # Build run arguments.
        run_args = [
            "run", "-d",
            "--name", container_name,
            "--cpu-shares", config.cpu_limit,
            "--memory", config.memory_limit,
            "--memory-swap", config.memory_limit,
            "--network", "none" if not config.network_enabled else "bridge",
            "--read-only",
            "--tmpfs", "/tmp:size=512m",
            "--tmpfs", "/var/tmp:size=256m",
        ]

        # Mount workspace if provided.
        if config.workspace_root:
            run_args.extend(["-v", f"{config.workspace_root}:/workspace:rw"])

        run_args.append(config.image)
        run_args.extend(["sleep", "3600"])

        result = _docker(*run_args, timeout=60.0)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start sandbox: {result.stderr}")

        self._containers[sandbox_id] = {
            "name": container_name,
            "config": config,
            "started_at": time.time(),
        }
        return sandbox_id

    def execute(
        self,
        sandbox_id: str,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandOutput:
        """Execute a command inside the running sandbox container."""
        if sandbox_id not in self._containers:
            return CommandOutput(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=0,
                error=f"Sandbox {sandbox_id} not found",
            )

        container = self._containers[sandbox_id]
        container_name = container["name"]
        cmd_timeout = timeout or container["config"].command_timeout

        # Build docker exec command.
        exec_args = ["exec"]
        if cwd:
            exec_args.extend(["-w", cwd])
        exec_args.extend([container_name, "sh", "-c", command])

        start = time.monotonic()
        try:
            result = _docker(*exec_args, timeout=cmd_timeout)
            return CommandOutput(
                exit_code=result.returncode,
                stdout=_decode(result.stdout),
                stderr=_decode(result.stderr),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandOutput(
                exit_code=-1,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=True,
                error=f"Command timed out after {cmd_timeout}s",
            )
        except FileNotFoundError:
            return CommandOutput(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=int((time.monotonic() - start) * 1000),
                error="docker command not found",
            )

    def stop(self, sandbox_id: str) -> None:
        """Stop and remove the sandbox container."""
        if sandbox_id not in self._containers:
            return

        container = self._containers.pop(sandbox_id)
        container_name = container["name"]

        with contextlib.suppress(Exception):
            _docker("stop", "-t", "5", container_name, timeout=15.0)
        with contextlib.suppress(Exception):
            _docker("rm", "-f", container_name, timeout=10.0)

    def copy_to(self, sandbox_id: str, host_path: str, container_path: str) -> None:
        """Copy a file/directory from host into the sandbox container."""
        if sandbox_id not in self._containers:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")

        container_name = self._containers[sandbox_id]["name"]
        result = _docker("cp", host_path, f"{container_name}:{container_path}", timeout=30.0)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to copy to sandbox: {result.stderr}")

    def copy_from(self, sandbox_id: str, container_path: str, host_path: str) -> None:
        """Copy a file/directory from the sandbox container to host."""
        if sandbox_id not in self._containers:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")

        container_name = self._containers[sandbox_id]["name"]
        result = _docker("cp", f"{container_name}:{container_path}", host_path, timeout=30.0)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to copy from sandbox: {result.stderr}")

    def is_running(self, sandbox_id: str) -> bool:
        """Check if a sandbox container is still running."""
        if sandbox_id not in self._containers:
            return False

        container_name = self._containers[sandbox_id]["name"]
        result = _docker("inspect", "-f", "{{.State.Running}}", container_name, timeout=10.0)
        return result.returncode == 0 and "true" in result.stdout.lower()
