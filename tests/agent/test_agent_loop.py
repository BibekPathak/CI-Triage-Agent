"""End-to-end agent loop test using a deterministic broken repo.

No live LLM, no network, no Git, no Docker. This proves the full pipeline
(collect -> diagnose -> plan -> reproduce -> patch -> verify -> approval) works
with a completely scripted model.
"""

from __future__ import annotations

import pytest

from app.agent import Budget, Executor, Orchestrator, Planner
from app.agent.prompts import PatchProposal
from app.llm import build_provider
from app.models import RunStatus, TriageState, VerificationLevel
from app.observability.events import EventRecorder


def _make_broken_repo(tmp_path) -> str:
    """Create a repo with a sign bug: add() returns a-b instead of a+b."""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    # conftest at root so `src` is importable from tests/.
    (tmp_path / "conftest.py").write_text("")
    (src / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    (tests / "test_calc.py").write_text(
        "from src.calc import add, sub\n\n\n"
        "def test_add():\n    assert add(2, 2) == 4\n\n\n"
        "def test_sub():\n    assert sub(5, 3) == 2\n"
    )
    return str(tmp_path)


CI_LOGS = (
    "Run #42 FAILED\n"
    "job: test\n"
    "FAILED tests/test_calc.py::test_add\n"
    "Traceback (most recent call last)\n"
    "  src/calc.py:2\n"
    "AssertionError: assert (2 - 2) == 4\n"
)


def _build_deterministic(llm_extra: dict) -> tuple[Executor, EventRecorder, object]:
    recorder = EventRecorder()

    def handler(model, user: str):
        if model is PatchProposal:
            return llm_extra.get(
                "patch",
                {
                    "file": "src/calc.py",
                    "original": "def add(a, b):\n    return a - b",
                    "replacement": "def add(a, b):\n    return a + b",
                    "explanation": "Fix sign bug so add returns a+b.",
                    "risk": "low",
                },
            )
        if model.__name__ == "Diagnosis":
            return {"root_cause": "wrong operator in add", "report": [], "evidence": ["log"],
                    "confidence": 0.9, "error_category": "deterministic_code",
                    "next_action": "inspect src/calc.py", "reasoning_summary": "sign bug"}
        if model.__name__ == "RootCauseAnalysis":
            return {"hypotheses": [{"description": "sign bug in add", "confidence": 0.9,
                                    "verification_strategy": "run test_add"}],
                    "selected_hypothesis": "sign bug in add", "confidence": 0.9,
                    "reasoning_summary": "sign bug"}
        if model.__name__ == "Plan":
            return {"goal": "fix add", "steps": ["read src/calc.py", "patch add"], "rationale": "x"}
        if model.__name__ == "VerificationDecision":
            return {"verdict": "success", "next_action": "done", "reasoning_summary": "passes"}
        raise AssertionError(f"unexpected model: {model}")

    llm = build_provider(provider="deterministic", structured_handler=handler)
    executor = Executor()
    return executor, recorder, llm


@pytest.mark.asyncio
async def test_full_pipeline_resolves_failure(tmp_path):
    workspace = _make_broken_repo(tmp_path)
    executor, recorder, llm = _build_deterministic({})
    planner = Planner(llm, recorder)
    orch = Orchestrator(
        planner, executor, recorder, Budget(max_iterations=3), workspace_root=workspace
    )

    state = TriageState(repository="demo/calc", workflow_run_id="42")

    async def context_source(s):
        s.ci_logs = CI_LOGS
        s.repository_context = {"summary": "simple calc repo"}

    outcome = await orch.run(state, context_source)
    assert outcome.success, outcome.stop_reason
    assert state.status == RunStatus.AWAITING_APPROVAL
    assert state.approval_required is True

    # The buggy line got fixed in the workspace.
    content = (tmp_path / "src" / "calc.py").read_text()
    assert "return a + b" in content
    assert "src/calc.py" in state.changed_files

    # Reproduction confirmed the pre-fix failure, then the fix passed.
    levels = [r.level.value for r in state.verification.results]
    assert levels.count("original") == 2  # reproduce (FAIL) + verify (PASS)
    assert all(
        r.status.value == "PASS"
        for r in state.verification.results
        if r.level.value in ("full", "related", "lint")
    )
    assert state.verification.status_for(VerificationLevel.ORIGINAL).value == "PASS"

    # The first original run (reproduction) showed the actual failure.
    assert state.verification.results[0].status.value == "FAIL"

    # Observability trace populated with the expected pipeline phases.
    steps = {e.step for e in recorder.events}
    assert {"collect", "reproduce", "patch", "verify", "prepare_pr"}.issubset(steps)


@pytest.mark.asyncio
async def test_infrastructure_failure_not_patched(tmp_path):
    workspace = _make_broken_repo(tmp_path)
    executor, recorder, llm = _build_deterministic({})
    planner = Planner(llm, recorder)
    orch = Orchestrator(planner, executor, recorder, workspace_root=workspace)
    state = TriageState(repository="demo/calc", workflow_run_id="42")

    async def ctx(s):
        s.ci_logs = "npm ERR! code ENOTFOUND\nCould not resolve dependency 'foo'"

    outcome = await orch.run(state, ctx)
    assert outcome.stop_reason == "infrastructure"
    assert state.approval_required is False
    # No patch should have modified the workspace.
    content = (tmp_path / "src" / "calc.py").read_text()
    assert "return a - b" in content
    assert state.changed_files == []
