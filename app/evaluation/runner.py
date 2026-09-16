"""Benchmark runner.

Runs each :class:`BenchmarkScenario` through a real (deterministic) agent loop
against a materialized local git repo, then validates the outcome against the
scenario's expectations: reproduction of the original failure, correct fix
applied to the expected file only, the original failing check passing, and no
regression.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from app.evaluation.models import BenchmarkScenario, ScenarioResult
from app.evaluation.scenarios import write_repo_files


def _run_scenario(scenario: BenchmarkScenario, workspace: str) -> ScenarioResult:
    """Run one scenario through the deterministic agent loop."""
    from pathlib import Path

    from app.agent.executor import Executor
    from app.agent.orchestrator import Budget, Orchestrator
    from app.agent.planner import Planner
    from app.agent.prompts import PatchProposal
    from app.llm.deterministic import DeterministicLLM
    from app.models.state import TriageState
    from app.observability.events import EventRecorder

    start = time.monotonic()

    # 1. Materialize the broken repo.
    write_repo_files(scenario, workspace)
    repo_root = Path(workspace)
    _git(repo_root, "init", "-q", "-b", "main")
    _set_identity(repo_root)
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    # 2. Build a deterministic agent that fixes THIS scenario's sign bug.
    def handler(model, user: str):
        if model is PatchProposal:
            return {
                "file": scenario.fix_file,
                "original": scenario.fix_original,
                "replacement": scenario.fix_replacement,
                "explanation": scenario.expected_root_cause,
                "risk": "low",
            }
        if model.__name__ == "Diagnosis":
            return {
                "root_cause": scenario.expected_root_cause, "report": [],
                "evidence": ["log"], "confidence": 0.9,
                "error_category": "deterministic_code",
                "next_action": f"inspect {scenario.fix_file}",
                "reasoning_summary": scenario.expected_root_cause,
            }
        if model.__name__ == "RootCauseAnalysis":
            return {
                "hypotheses": [
                    {"description": scenario.expected_root_cause, "confidence": 0.9,
                     "verification_strategy": f"run {scenario.name}"}
                ],
                "selected_hypothesis": scenario.expected_root_cause,
                "confidence": 0.9, "reasoning_summary": scenario.expected_root_cause,
            }
        if model.__name__ == "Plan":
            return {"goal": "fix", "steps": [f"read {scenario.fix_file}", "patch"],
                    "rationale": "x"}
        if model.__name__ == "VerificationDecision":
            return {"verdict": "success", "next_action": "done",
                    "reasoning_summary": "passes"}
        return {}

    llm = DeterministicLLM(structured_handler=handler)
    recorder = EventRecorder()
    planner = Planner(llm=llm, recorder=recorder)
    executor = Executor()
    orch = Orchestrator(
        planner=planner,
        executor=executor,
        recorder=recorder,
        budget=Budget(max_iterations=3),
        workspace_root=str(repo_root),
    )

    state = TriageState(repository=scenario.repository, workflow_run_id="1")
    recorder.bind(state.triage_id)

    # The failing/test context mirrors the scenario.
    func_name = scenario.fix_original.split("def ", 1)[-1].split("(", 1)[0].strip()
    ci_logs = (
        f"Run #1 FAILED\n"
        f"job: test\n"
        f"FAILED {scenario.fix_file}::test_{func_name}\n"
        f"Traceback (most recent call last)\n"
        f"  {scenario.fix_file}:2\n"
        f"AssertionError: assert (2 - 2) == 4\n"
    )

    async def _local_context(s: TriageState) -> None:
        s.ci_logs = ci_logs

    # 3. Reproduce pre-fix failure.
    reproduced = _command_succeeds(str(repo_root), scenario.failing_command) is False

    outcome = asyncio.run(orch.run(state, _local_context))
    result_state = outcome.state

    # 4. Validate post-fix.
    fix_file_path = repo_root / scenario.fix_file
    original_resolved = bool(scenario.fix_replacement in fix_file_path.read_text())
    relevant_passed = _command_succeeds(str(repo_root), scenario.failing_command)
    validation_passes = _command_succeeds(str(repo_root), scenario.validation_command)
    no_regression = validation_passes

    extra_files = [
        f for f in result_state.changed_files
        if f.lstrip("/") not in expected_set(scenario)
    ]
    no_unexpected = not extra_files

    elapsed_ms = int((time.monotonic() - start) * 1000)
    succeeded = (
        outcome.success
        and reproduced
        and original_resolved
        and relevant_passed
        and no_regression
        and no_unexpected
    )

    return ScenarioResult(
        scenario=scenario,
        succeeded=succeeded,
        reproduced=reproduced,
        root_cause_matched=(
            scenario.expected_root_cause in (result_state.root_cause or "")
        ),
        patch_applied=original_resolved,
        original_resolved=original_resolved,
        relevant_tests_passed=relevant_passed,
        no_regression=no_regression,
        extra_files_changed=extra_files,
        iterations=result_state.iterations,
        tool_calls=result_state.tool_calls,
        llm_calls=result_state.llm_calls,
        execution_time_ms=elapsed_ms,
        reason=outcome.stop_reason,
        state=result_state,
    )


def run_benchmark(scenarios: Sequence[BenchmarkScenario]) -> list[ScenarioResult]:
    """Run all scenarios and return their results."""
    import tempfile


    results = []
    with tempfile.TemporaryDirectory(prefix="cta-bench-") as tmp:
        # Each scenario gets its own subdirectory.
        for i, scenario in enumerate(scenarios):
            import pathlib

            ws = pathlib.Path(tmp) / str(i)
            ws.mkdir(parents=True, exist_ok=True)
            results.append(_run_scenario(scenario, str(ws)))
    return results


def report(results: Sequence[ScenarioResult]) -> None:
    """Print a human-readable benchmark report to stdout."""
    import json

    from rich.console import Console
    from rich.table import Table

    from app.evaluation.metrics import aggregate

    console = Console()
    table = Table(title="Benchmark Results")
    table.add_column("Scenario", style="cyan")
    table.add_column("Succeeded")
    table.add_column("Reproduced")
    table.add_column("Root Cause")
    table.add_column("Patch")
    table.add_column("Resolved")
    table.add_column("No Regression")
    for r in results:
        _y = "[green]Y[/green]"
        _n = "[red]N[/red]"
        table.add_row(
            r.scenario.name,
            _y if r.succeeded else _n,
            _y if r.reproduced else _n,
            _y if r.root_cause_matched else _n,
            _y if r.patch_applied else _n,
            _y if r.original_resolved else _n,
            _y if r.no_regression else _n,
        )
    console.print(table)
    console.print(json.dumps(aggregate(results), indent=2, default=str))


def _command_succeeds(root: str, command: str) -> bool:
    import shlex
    import subprocess

    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def expected_set(scenario: BenchmarkScenario) -> set[str]:
    return {f.lstrip("/") for f in scenario.expected_files}


def _git(root, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def _set_identity(root) -> None:
    for key, val in (
        ("user.name", "t"),
        ("user.email", "t@t"),
    ):
        _git(root, "config", key, val)
