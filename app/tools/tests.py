"""Test/lint/typecheck execution tools.

These build safe commands (targeted test, related suite, full suite, linter,
type checker) and run them through the command runner. They parse pass/fail
counts into a :class:`TestResult`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, PrivateAttr

from app.models.domain import ActionClass, CheckStatus, TestResult, ToolResult, VerificationLevel
from app.tools.base import Tool


def _default_runner():
    from app.sandbox.runners import LocalCommandRunner
    return LocalCommandRunner()

# pytest summary lines: "1 passed in 0.05s", "1 failed in 0.05s",
# "47 passed, 2 failed, 1 skipped in 1.2s"
_PYTEST_PASSED = re.compile(r"(?<![\d])(\d+)\s+passed")
_PYTEST_FAILED = re.compile(r"(?<![\d])(\d+)\s+failed")
_PYTEST_SKIPPED = re.compile(r"(?<![\d])(\d+)\s+skipped")


class RunTestsParams(BaseModel):
    command: str = Field(description="Test command to run")
    level: VerificationLevel = Field(description="Which verification layer this run represents")
    cwd: str | None = Field(default=None, description="Working directory to run in")
    timeout: float | None = None


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Run a test command and parse pass/fail/skip counts."
    params = RunTestsParams
    action_class = ActionClass.SANDBOX_WRITE

    _runner = PrivateAttr(default_factory=_default_runner)

    def set_runner(self, runner) -> None:
        self._runner = runner

    def run(self, args: dict) -> ToolResult:
        out = self._runner.run(args["command"], timeout=args.get("timeout"), cwd=args.get("cwd"))
        level = args["level"]
        text = out.stdout or ""
        fm = _PYTEST_FAILED.search(text)
        pm = _PYTEST_PASSED.search(text)
        sm = _PYTEST_SKIPPED.search(text)
        if pm or fm:
            passed = int(pm.group(1)) if pm else 0
            failed = int(fm.group(1)) if fm else 0
            skipped = int(sm.group(1)) if sm else 0
        else:
            passed, failed, skipped = (0, 0, 0)
            if out.exit_code == 0 and not text:
                passed = 1  # success but unrecognized/empty format
        status = CheckStatus.PASS if out.exit_code == 0 else CheckStatus.FAIL
        result = TestResult(
            level=level,
            command=args["command"],
            status=status,
            passed=passed,
            failed=failed,
            skipped=skipped,
            error=out.error or (out.stderr[:2000] if out.stderr else None),
            duration_ms=out.duration_ms,
            output=out.stdout[-4000:],
        )
        return ToolResult(
            tool=self.name,
            exit_code=out.exit_code,
            stdout=out.stdout,
            stderr=out.stderr,
            duration_ms=out.duration_ms,
            ok=out.exit_code == 0,
            data={"level": level.value, "test_result": result.model_dump()},
            error=out.error,
        )
