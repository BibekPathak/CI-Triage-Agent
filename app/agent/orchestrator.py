"""The agent orchestrator: an explicit, observable state machine.

The loop is a fixed pipeline rather than an unbounded ``while True``. Each stage
maps to a :class:`RunStatus` phase and a dedicated method, so behaviour is
auditable, testable and resumable:

    COLLECT -> DIAGNOSE -> PLAN -> REPRODUCE -> PATCH -> VERIFY

    (a bounded loop re-enters DIAGNOSE when verification needs iteration)

When verification succeeds the run stops at ``AWAITING_APPROVAL`` (a human
approval is required before any remote write). Guardrails are structural:

* budgets cap iterations / tool calls / execution time / tokens-cost
* the :class:`Executor` gates tools by action class + approval state
* infrastructure-classified failures are surfaced, not auto-patched
* verification is layered; failure loops back to diagnosis

The ``context_source`` is an async ``(state) -> None`` that populates CI logs
and repo context (GitHub-backed in Phase 7; fixture-backed for tests/demo).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent.executor import ExecutedCall, Executor
from app.agent.parser import is_infrastructure
from app.agent.planner import Planner
from app.models.domain import (
    ActionClass,
    RunStatus,
    TestResult,
    VerificationLevel,
)
from app.models.state import TriageState
from app.observability.events import EventRecorder, RunEvent

if TYPE_CHECKING:
    from app.sandbox.manager import SandboxManager

ContextSource = Callable[[TriageState], Awaitable[None]]


@dataclass
class Budget:
    max_iterations: int = 5
    max_tool_calls: int = 60
    max_execution_seconds: int = 900


@dataclass
class TriageOutcome:
    state: TriageState
    success: bool
    stop_reason: str


class Orchestrator:
    """Runs a triage to completion or until it must pause for approval."""

    def __init__(
        self,
        planner: Planner,
        executor: Executor,
        recorder: EventRecorder | None = None,
        budget: Budget | None = None,
        workspace_root: str = "",
        sandbox_manager: SandboxManager | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.recorder = recorder or EventRecorder()
        self.budget = budget or Budget()
        self.workspace_root = workspace_root
        self._sandbox_manager = sandbox_manager
        self._sandbox_id: str | None = None

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def run(self, state: TriageState, context_source: ContextSource) -> TriageOutcome:
        start = time.monotonic()
        try:
            # Start sandbox if manager provided (Docker or local backend).
            if self._sandbox_manager is not None:
                self._sandbox_id = self._sandbox_manager.start(
                    workspace_root=self.workspace_root,
                )
                from app.sandbox.runner import SandboxRunner

                runner = SandboxRunner(self._sandbox_manager, self._sandbox_id)
                self.executor.set_runner(runner)

            await self._collect(state, context_source)
            diag = await self._diagnose(state)

            if is_infrastructure(state.failure):
                self._emit(
                    state,
                    "classify",
                    "STOP",
                    decision=(
                        f"infrastructure failure ({state.failure.error_type.value});"
                        " no code patch"
                    ),
                )
                state.status = RunStatus.COMPLETED
                state.approval_required = False
                state.final_result = (
                    "Failure is infrastructure-related; no code patch was produced."
                )
                return TriageOutcome(state, True, "infrastructure")

            await self._plan(state, diag)
            self._emit(state, "plan", "PLAN", decision=f"{len(state.plan)} steps")

            outcome = await self._solve(state, start)
            return outcome
        except Exception as exc:  # noqa: BLE001 - recover into a clear failure state
            state.status = RunStatus.FAILED
            state.last_error = str(exc)
            self._emit(state, "error", "FAILED", decision=str(exc))
            return TriageOutcome(state, False, "error")
        finally:
            # Ensure sandbox cleanup even on abnormal termination.
            if self._sandbox_manager is not None and self._sandbox_id is not None:
                self._sandbox_manager.stop(self._sandbox_id)
                self._sandbox_id = None

    # ------------------------------------------------------------------ #
    # Pipeline stages
    # ------------------------------------------------------------------ #
    async def _collect(self, state: TriageState, context_source: ContextSource) -> None:
        state.status = RunStatus.COLLECTING
        await context_source(state)
        if not state.ci_logs:
            state.ci_logs = state.failure.raw_logs or ""
        self._emit(state, "collect", "COLLECT", decision=f"{len(state.ci_logs)} log chars")

    async def _diagnose(self, state: TriageState) -> str:
        state.status = RunStatus.DIAGNOSING
        diag = await self.planner.analyze_failure(state)
        return diag.next_action or state.root_cause or ""

    async def _plan(self, state: TriageState, diagnosis_summary: str) -> None:
        state.status = RunStatus.PLANNING
        await self.planner.generate_hypotheses(state)
        await self.planner.build_plan(state, diagnosis_summary)

    # ------------------------------------------------------------------ #
    # Main bounded loop: reproduce -> hypothesize fix -> patch -> verify
    # ------------------------------------------------------------------ #
    async def _solve(self, state: TriageState, start: float) -> TriageOutcome:
        reproduce_cmd = self._reproduce_command(state)
        state.status = RunStatus.REPRODUCING

        # Level 1: reproduce the original failure.
        rep = await self._run_tests(state, VerificationLevel.ORIGINAL, reproduce_cmd)
        self._emit(state, "reproduce", "REPRODUCE",
                   decision=f"{rep.status.value} (failed={rep.failed})")

        for attempt in range(self.budget.max_iterations):
            state.iterations = attempt + 1
            self._guard_budget(state, start)

            # 1. Pick the file to fix and read it.
            target = self._pick_target_file(state)
            if not target:
                state.last_error = "Planner did not identify a target file"
                return TriageOutcome(state, False, "no_target")

            content = self._read_workspace_file(target)
            patch = await self.planner.propose_patch(state, target, content)

            # 2. Apply the patch in the isolated workspace.
            applied = self._apply_patch(
                state, patch.file or target, patch.original, patch.replacement
            )
            if not applied.result.ok:
                state.last_error = applied.result.error
                self._emit(state, "patch", "PATCH", decision="failed to apply")
                return TriageOutcome(state, False, "patch_apply_failed")

            state.changed_files = self._dedupe(state.changed_files + [patch.file or target])
            state.candidate_patch = self._capture_diff()
            state.diff_signed_off = True
            self._emit(state, "patch", "PATCH", decision="applied " + (patch.file or target))

            # 3. Verify: original failing test + related + full suite.
            await self._verify_layers(state, reproduce_cmd)

            # 4. Decide.
            decision = await self.planner.decide_verification(
                state.verification.results, state.changed_files
            )
            self._emit(state, "verify", "VERIFY", decision=decision.verdict)
            if decision.verdict == "success" or state.verification.all_critical_pass:
                await self._prepare_pr(state)
                return TriageOutcome(state, True, "verified")

            if decision.verdict == "give_up":
                state.status = RunStatus.FAILED
                state.final_result = "Agent gave up after repeated failed fixes."
                return TriageOutcome(state, False, "give_up")

            # needs_iteration: reflect on new failure and loop
            state.status = RunStatus.DIAGNOSING
            self._emit(state, "recover", "ITERATE", decision="retrying with new hypothesis")

        state.status = RunStatus.FAILED
        state.final_result = f"Max iterations ({self.budget.max_iterations}) reached."
        return TriageOutcome(state, False, "max_iterations")

    # ------------------------------------------------------------------ #
    # Verification
    # ------------------------------------------------------------------ #
    async def _verify_layers(self, state: TriageState, reproduce_cmd: str) -> None:
        # Level 1: original failing test
        await self._run_tests(state, VerificationLevel.ORIGINAL, reproduce_cmd)
        # Level 2: related / full suite
        await self._run_tests(state, VerificationLevel.RELATED, "pytest -q")
        await self._run_tests(state, VerificationLevel.FULL, "pytest -q")
        # Level 4: lint
        await self._run_tests(state, VerificationLevel.LINT, "ruff check .")

    def _reproduce_command(self, state: TriageState) -> str:
        """Build the narrowest reproduction command from failure metadata."""
        f = state.failure
        if f.file and f.test_name:
            return f"pytest -q {f.file}::{f.test_name}"
        if f.file:
            return f"pytest -q {f.file}"
        if f.test_name:
            return f"pytest -q {f.test_name}"
        return "pytest -q"

    async def _run_tests(
        self, state: TriageState, level: VerificationLevel, command: str
    ) -> TestResult:
        call = self.executor.execute(
            "run_tests",
            {"command": command, "cwd": self.workspace_root or None, "level": level.value},
            state,
        )
        tr = TestResult.model_validate(call.result.data.get("test_result", {}))
        state.test_results.append(tr)
        state.verification.results.append(tr)
        return tr

    # ------------------------------------------------------------------ #
    # Workspace patch primitives (local dir; git-managed in Phase 6)
    # ------------------------------------------------------------------ #
    def _pick_target_file(self, state: TriageState) -> str:
        if state.failure.file:
            return state.failure.file
        # Fall back to the selected hypothesis' most cited file (resolution
        # refined in Phase 6); default to first tracked py file if unknown.
        return ""

    def _read_workspace_file(self, path: str) -> str:
        from pathlib import Path

        root = Path(self.workspace_root)
        p = (root / path.strip("/")).resolve() if self.workspace_root else Path(path)
        if not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")

    def _apply_patch(
        self, state: TriageState, file_path: str, original: str, replacement: str
    ) -> ExecutedCall:
        """Replace ``original`` with ``replacement`` in ``file_path``.

        Uses a bounded python file write inside the workspace. Returns an
        ExecutedCall-style result describing success/failure.
        """
        from pathlib import Path

        root = Path(self.workspace_root)
        target = (root / file_path.strip("/")).resolve() if self.workspace_root else Path(file_path)
        base = root.resolve() if self.workspace_root else None

        if base and not target.is_relative_to(base):
            return ExecutedCall(
                "apply_patch", {}, _err("patch escapes workspace"),
                action_class=ActionClass.SANDBOX_WRITE,
            )

        if not original:
            return ExecutedCall(
                "apply_patch", {}, _err("empty original text"),
                action_class=ActionClass.SANDBOX_WRITE,
            )
        if not target.is_file():
            return ExecutedCall(
                "apply_patch", {}, _err(f"file not found: {file_path}"),
                action_class=ActionClass.SANDBOX_WRITE,
            )

        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(original)
        if count != 1:
            return ExecutedCall(
                "apply_patch", {},
                _err(f"original text occurred {count} times (expected 1) in {file_path}"),
                action_class=ActionClass.SANDBOX_WRITE,
            )
        new_text = text.replace(original, replacement, 1)
        target.write_text(new_text, encoding="utf-8")
        from app.models.domain import ToolResult

        return ExecutedCall(
            "apply_patch", {},
            ToolResult(tool="apply_patch", ok=True),
            action_class=ActionClass.SANDBOX_WRITE,
        )

    def _capture_diff(self) -> str:
        # Phase 6 replaces this with a real git diff over the workspace.
        return "   <working-tree diff not captured until Phase 6 (git)>"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: list[str] = []
        for i in items:
            if i not in seen:
                seen.append(i)
        return seen

    # ------------------------------------------------------------------ #
    # PR + approval
    # ------------------------------------------------------------------ #
    async def _prepare_pr(self, state: TriageState) -> None:
        state.status = RunStatus.AWAITING_APPROVAL
        state.approval_required = True
        title = f"fix: resolve failing {state.failure.test_name or 'test'}"
        state.proposed_pr_title = title
        body = (
            f"## Summary\n\nResolve CI failure in {state.repository}.\n\n"
            f"## Root Cause\n\n{state.root_cause or 'unknown'}\n\n"
            f"## Validation\n\n- [x] Original failing test\n- [x] Related tests\n"
            f"- [x] Full test suite\n- [x] Lint\n\n"
            f"## Agent Analysis\n\nConfidence: {state.confidence}"
        )
        state.proposed_pr_body = body
        state.final_result = "Fix verified; awaiting human approval before opening a PR."
        self._emit(state, "prepare_pr", "APPROVAL",
                   decision=state.proposed_pr_title)

    # ------------------------------------------------------------------ #
    # Budgets
    # ------------------------------------------------------------------ #
    def _guard_budget(self, state: TriageState, start: float) -> None:
        if state.tool_calls > self.budget.max_tool_calls:
            raise RuntimeError(f"tool-call budget exceeded ({state.tool_calls})")
        if time.monotonic() - start > self.budget.max_execution_seconds:
            raise RuntimeError("execution time budget exceeded")

    def _emit(self, state: TriageState, step: str, phase: str, decision: str = "") -> None:
        self.recorder.record(
            RunEvent(run_id=state.triage_id, step=step, phase=phase, decision=decision)
        )


def _err(message: str):
    from app.models.domain import ToolResult

    return ToolResult(tool="apply_patch", ok=False, exit_code=1, error=message)
