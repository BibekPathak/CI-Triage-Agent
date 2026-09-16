"""Evaluation domain models.

Defines a :class:`BenchmarkScenario` (a deterministic, reproducible local
failure to fix) and the rich :class:`ScenarioResult` produced by running an
agent against it. Success is *earned*: the failure is reproduced, the correct
fix is applied, the original failing check passes, and no regression occurs --
not merely "the LLM said it solved it".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.state import TriageState


@dataclass
class BenchmarkScenario:
    """A single deterministic local scenario.

    ``files`` maps relative paths to file contents (the broken repo template).
    ``original``/``replacement`` drive the deterministic fixer. The scenario is
    resolved offline (no network, no API key) against a real local repo.
    """

    name: str
    repository: str
    failing_command: str
    expected_root_cause: str
    expected_files: list[str]
    validation_command: str
    files: dict[str, str] = field(default_factory=dict)
    fix_file: str = ""
    fix_original: str = ""
    fix_replacement: str = ""


@dataclass
class ScenarioResult:
    """Outcome of running one scenario through the agent."""

    scenario: BenchmarkScenario
    succeeded: bool
    reproduced: bool = False
    root_cause_matched: bool = False
    patch_applied: bool = False
    original_resolved: bool = False
    relevant_tests_passed: bool = False
    no_regression: bool = True
    extra_files_changed: list[str] = field(default_factory=list)
    iterations: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    execution_time_ms: int = 0
    reason: str = ""
    state: TriageState | None = None

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario.name,
            "succeeded": self.succeeded,
            "reproduced": self.reproduced,
            "root_cause_matched": self.root_cause_matched,
            "patch_applied": self.patch_applied,
            "original_resolved": self.original_resolved,
            "relevant_tests_passed": self.relevant_tests_passed,
            "no_regression": self.no_regression,
            "extra_files_changed": self.extra_files_changed,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "execution_time_ms": self.execution_time_ms,
            "reason": self.reason,
        }
