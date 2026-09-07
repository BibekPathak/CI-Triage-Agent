"""Shell execution tool.

Runs a command inside the sandbox via a :class:`CommandRunner`. Policy is
enforced by the runner before execution; the tool never runs host commands
directly. The active runner can be swapped (Local vs Docker) through the
``runner`` attribute, set by the orchestrator.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, PrivateAttr

from app.models.domain import ActionClass, ToolResult
from app.sandbox.runners import LocalCommandRunner
from app.tools.base import Tool


class RunCommandParams(BaseModel):
    command: str = Field(description="Command line to execute in the sandbox")
    timeout: float | None = Field(default=None, description="Optional per-command timeout (s)")
    cwd: str | None = Field(default=None, description="Working directory")


class ShellTool(Tool):
    name = "run_command"
    description = (
        "Execute a validated command inside the isolated sandbox. "
        "Returns stdout/stderr/exit code."
    )
    params = RunCommandParams
    action_class = ActionClass.SANDBOX_WRITE

    _runner = PrivateAttr(default_factory=LocalCommandRunner)

    def set_runner(self, runner) -> None:
        self._runner = runner

    def run(self, args: dict) -> ToolResult:
        out = self._runner.run(
            args["command"],
            timeout=args.get("timeout"),
            cwd=args.get("cwd"),
        )
        return ToolResult(
            tool=self.name,
            exit_code=out.exit_code,
            stdout=out.stdout,
            stderr=out.stderr,
            duration_ms=out.duration_ms,
            ok=(out.exit_code == 0 and not out.denied),
            data={"timed_out": out.timed_out, "denied": out.denied},
            error=out.error,
        )
