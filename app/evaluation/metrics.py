"""Evaluation metrics: aggregate benchmark results into summary metrics."""

from __future__ import annotations

from collections.abc import Sequence

from app.evaluation.models import ScenarioResult


def aggregate(results: Sequence[ScenarioResult]) -> dict:
    """Aggregate per-scenario results into benchmark-level metrics.

    Metrics (per spec):
    - task_success_rate: full success (reproduced + fixed + resolved + pass)
    - root_cause_accuracy
    - patch_success_rate
    - regression_rate
    - unnecessary_file_change_rate
    - average_iterations / tool_calls / llm_calls / execution_time_ms
    """
    if not results:
        return {
            "count": 0,
            "task_success_rate": 0.0,
            "root_cause_accuracy": 0.0,
            "patch_success_rate": 0.0,
            "regression_rate": 0.0,
            "unnecessary_file_change_rate": 0.0,
            "average_iterations": 0.0,
            "average_tool_calls": 0.0,
            "average_llm_calls": 0.0,
            "average_execution_time_ms": 0.0,
        }

    n = len(results)
    success = sum(1 for r in results if r.succeeded)
    root_cause_matched = sum(1 for r in results if r.root_cause_matched)
    patch = sum(1 for r in results if r.patch_applied)
    regression = sum(
        1 for r in results if r.patch_applied and not r.no_regression
    )
    extra_files = sum(
        1 for r in results if r.extra_files_changed
    )

    return {
        "count": n,
        "task_success_rate": success / n,
        "root_cause_accuracy": root_cause_matched / n,
        "patch_success_rate": patch / n,
        "regression_rate": regression / max(patch, 1),
        "unnecessary_file_change_rate": extra_files / n,
        "average_iterations": sum(r.iterations for r in results) / n,
        "average_tool_calls": sum(r.tool_calls for r in results) / n,
        "average_llm_calls": sum(r.llm_calls for r in results) / n,
        "average_execution_time_ms": sum(r.execution_time_ms for r in results) / n,
    }
