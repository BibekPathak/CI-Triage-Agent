"""Tool-calling executor.

The :class:`Executor` is the harness that runs a single tool invocation inside
the guardrails of the agent loop:

* policy enforcement (a tool's declared action class + approval state decide
  whether it may run at all - never the LLM)
* central shell-runner injection (Docker vs local) so all tools share it
* per-tool timeouts and bounded retries
* telemetry (tool call count, duration, failures) written back to state

The orchestrator drives the loop; this class executes one step reliably and
recovers from expected failures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.agent.policies import PolicyEngine
from app.models.domain import ActionClass, ToolResult
from app.models.state import TriageState
from app.tools.base import ToolRegistry
from app.tools.registry import default_registry


@dataclass
class ExecutorOptions:
    max_retries: int = 1
    retry_backoff_s: float = 0.2


@dataclass
class ExecutedCall:
    tool: str
    args: dict
    result: ToolResult
    action_class: ActionClass
    retries: int = 0
    denied: bool = False
    events: list[dict] = field(default_factory=list)


class Executor:
    """Runs validated tool calls with policy enforcement and retries."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        options: ExecutorOptions | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.policy = policy or PolicyEngine(approve_mode="manual")
        self.options = options or ExecutorOptions()

    def set_runner(self, runner) -> None:
        """Inject the command runner (Docker or local) into shell/tests tools."""
        for name in ("run_command",):
            tool = self.registry.get(name)
            if hasattr(tool, "set_runner"):
                tool.set_runner(runner)
        for name in ("run_tests",):
            tool = self.registry.get(name)
            if hasattr(tool, "set_runner"):
                tool.set_runner(runner)

    def execute(
        self,
        tool_name: str,
        args: dict,
        state: TriageState,
        *,
        approval_granted: bool = False,
    ) -> ExecutedCall:
        """Execute one tool call against ```state```, enforcing policy.

        Returns an :class:`ExecutedCall`; never raises for expected failures
        (unknown tool, denied action, tool error). Increments ``tool_calls``
        on state for budget accounting.
        """
        start = time.monotonic()
        denied = False
        event: dict | None = None

        # 1. Resolve tool + action class for policy.
        try:
            tool = self.registry.get(tool_name)
        except KeyError:
            result = ToolResult(
                tool=tool_name, ok=False, exit_code=1, error=f"Unknown tool: {tool_name}"
            )
            return ExecutedCall(tool_name, args, result, ActionClass.READ_ONLY)

        action_class = tool.action_class

        # 2. Policy gate (programmatic; LLM never decides this).
        if not self.policy.check(action_class, approval_granted if action_class in
                                 (ActionClass.REMOTE_WRITE, ActionClass.DESTRUCTIVE) else True):
            denied = True
            result = ToolResult(
                tool=tool_name,
                ok=False,
                exit_code=-1,
                error=f"Action '{action_class.value}' requires approval",
            )
            state.tool_calls += 1
            return ExecutedCall(tool_name, args, result, action_class, denied=denied)

        # 3. Run with bounded retries for transient tool exceptions.
        retries = 0
        while True:
            try:
                result = tool.execute(args)
                result.tool = tool_name
                break
            except Exception as exc:  # noqa: BLE001 - recover structurally
                if retries < self.options.max_retries:
                    retries += 1
                    time.sleep(self.options.retry_backoff_s)
                    continue
                result = ToolResult(tool=tool_name, ok=False, exit_code=-1, error=str(exc))
                break

        result.duration_ms = int((time.monotonic() - start) * 1000)
        state.tool_calls += 1

        event = {
            "tool": tool_name,
            "arguments": args,
            "action_class": action_class.value,
            "result": result.model_dump(exclude={"stdout", "stderr"}),
            "duration_ms": result.duration_ms,
            "ok": result.ok,
        }
        return ExecutedCall(
            tool_name, args, result, action_class,
            retries=retries, events=[event] if event else [],
        )
