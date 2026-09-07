"""Command runner abstraction for the shell tool.

A :class:`CommandRunner` executes a command and returns structured output. The
default :class:`LocalCommandRunner` runs locally (used by tests and the
deterministic demo); the Docker-backed runner is provided in Phase 5 and shares
this interface so tools are backend-agnostic.

Every runner applies :func:`app.agent.policies.validate_command` before running,
so policy enforcement is uniform regardless of backend.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol

from app.agent.policies import DEFAULT_POLICY, CommandDeniedError, CommandPolicy


def _decode(value: bytes | str | None) -> str:
    """Normalize subprocess-produced output to text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


@dataclass
class CommandOutput:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    denied: bool = False
    error: str | None = None


class CommandRunner(Protocol):
    """Runs a validated command and returns structured output."""

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandOutput:
        ...


class LocalCommandRunner:
    """Runs commands as subprocesses on the host (policy enforced).

    Intended for tests and the deterministic demo. Projects MUST use the
    Docker-backed runner for real triage.
    """

    def __init__(self, policy: CommandPolicy = DEFAULT_POLICY) -> None:
        self.policy = policy

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> CommandOutput:
        start = time.monotonic()
        try:
            self.policy.validate_command(command, action_class=CommandPolicy().action_class)
        except CommandDeniedError as exc:
            return CommandOutput(
                exit_code=-1, stdout="", stderr="", duration_ms=0, denied=True, error=str(exc)
            )
        shell_timeout = timeout if timeout is not None else 120.0
        args = shlex.split(command)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=shell_timeout,
                cwd=cwd,
                env=None,
            )
            return CommandOutput(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandOutput(
                exit_code=-1,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=True,
                error=f"Command timed out after {shell_timeout}s",
            )
        except FileNotFoundError as exc:
            return CommandOutput(
                exit_code=-1,
                stdout="",
                stderr="",
                duration_ms=int((time.monotonic() - start) * 1000),
                error=f"Command not found: {exc}",
            )
