"""Tests for cooperative run cancellation."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.cancellation import CancellationToken, CancelledError
from app.db.engine import Base
from app.db.repository import TriageRepository
from app.models import RunStatus, TriageState


@pytest.fixture()
def db_factory():
    """A factory producing (repo, session) pairs sharing one in-memory DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def make():
        session = TestSession()
        return TriageRepository(session), session

    yield make
    engine.dispose()


class TestCancellationToken:
    def test_initially_not_cancelled(self):
        assert CancellationToken().cancelled is False

    def test_cancel_sets_flag_and_reason(self):
        token = CancellationToken()
        token.cancel("too slow")
        assert token.cancelled is True
        assert token.reason == "too slow"

    def test_cancel_is_idempotent_keeps_first_reason(self):
        token = CancellationToken()
        token.cancel("first")
        token.cancel("second")
        assert token.reason == "first"

    def test_raise_if_not_cancelled_does_nothing(self):
        CancellationToken().raise_if_cancelled()

    def test_raise_if_cancelled_raises(self):
        token = CancellationToken()
        token.cancel("stop")
        with pytest.raises(CancelledError):
            token.raise_if_cancelled()

    def test_cancelled_error_is_runtime_error(self):
        assert issubclass(CancelledError, RuntimeError)


class TestOrchestratorCancellation:
    """The orchestrator unwinds cleanly into CANCELLED when the token fires."""

    def _make_orchestrator(self, tmp_path, token):
        from app.agent import Budget, Executor, Orchestrator, Planner
        from app.agent.prompts import PatchProposal
        from app.llm import build_provider
        from app.observability.events import EventRecorder

        def handler(model, user: str):
            if model is PatchProposal:
                return {
                    "file": "src/calc.py",
                    "original": "def add(a, b):\n    return a - b",
                    "replacement": "def add(a, b):\n    return a + b",
                    "explanation": "fix",
                    "risk": "low",
                }
            if model.__name__ == "Diagnosis":
                return {"root_cause": "x", "report": [], "evidence": ["log"],
                        "confidence": 0.9, "error_category": "deterministic_code",
                        "next_action": "inspect", "reasoning_summary": "x"}
            if model.__name__ == "RootCauseAnalysis":
                return {"hypotheses": [{"description": "x", "confidence": 0.9,
                                        "verification_strategy": "run"}],
                        "selected_hypothesis": "x", "confidence": 0.9,
                        "reasoning_summary": "x"}
            if model.__name__ == "Plan":
                return {"goal": "fix", "steps": ["read", "patch"], "rationale": "x"}
            if model.__name__ == "VerificationDecision":
                return {"verdict": "needs_iteration", "next_action": "retry",
                        "reasoning_summary": "still failing"}
            return {}

        llm = build_provider(provider="deterministic", structured_handler=handler)
        recorder = EventRecorder()
        planner = Planner(llm, recorder)
        executor = Executor()
        return Orchestrator(
            planner, executor, recorder, Budget(max_iterations=3),
            workspace_root=str(tmp_path), cancel_token=token,
        )

    @pytest.mark.asyncio
    async def test_pre_cancelled_run_returns_cancelled(self, tmp_path):
        token = CancellationToken()
        token.cancel("user aborted")
        orch = self._make_orchestrator(tmp_path, token)

        state = TriageState(repository="demo/calc", workflow_run_id="42")

        async def context_source(s):
            s.ci_logs = "some logs"

        outcome = await orch.run(state, context_source)
        assert outcome.success is False
        assert outcome.stop_reason == "cancelled"
        assert state.status == RunStatus.CANCELLED
        assert "cancelled" in (state.final_result or "")

    @pytest.mark.asyncio
    async def test_cancel_during_solve_loop(self, tmp_path):
        """Cancelling while verifying (mid-loop) stops the run as CANCELLED."""
        # Real broken repo so the loop can apply a patch and reach verification.
        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir()
        tests.mkdir()
        (tmp_path / "conftest.py").write_text("")
        (src / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (tests / "test_calc.py").write_text(
            "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
        )

        token = CancellationToken()
        orch = self._make_orchestrator(tmp_path, token)

        state = TriageState(repository="demo/calc", workflow_run_id="42")

        # Fire the token from inside the verify decision, i.e. mid-solve-loop.
        real_decide = orch.planner.decide_verification

        async def firing_decide(verification_results, changed_files):
            token.cancel("stop during verify")
            return await real_decide(verification_results, changed_files)

        orch.planner.decide_verification = firing_decide  # type: ignore[assignment]

        async def context_source(s):
            s.ci_logs = (
                "FAILED tests/test_calc.py::test_add\n"
                "AssertionError: assert (2 - 2) == 4\n"
            )
            s.failure.file = "tests/test_calc.py"
            s.failure.test_name = "test_add"

        outcome = await orch.run(state, context_source)
        assert outcome.stop_reason == "cancelled"
        assert state.status == RunStatus.CANCELLED

    def test_run_status_api_value_for_cancelled(self):
        assert RunStatus.CANCELLED.api_status() == "cancelled"


class TestCancelTriageService:
    def test_cancel_signals_token_and_marks_cancelled(self, db_factory):
        from app.api.service import (
            cancel_triage,
            register_token,
            unregister_token,
        )

        repo, session = db_factory()
        try:
            state = TriageState(
                repository="acme/x", workflow_run_id="1", status=RunStatus.RUNNING
            )
            repo.save_state(state)
            repo.commit()

            token = CancellationToken()
            register_token(state.triage_id, token)

            cancelled = cancel_triage(state.triage_id, repo)
            assert token.cancelled is True
            assert cancelled.status == RunStatus.CANCELLED
            assert "cancelled" in (cancelled.final_result or "")
        finally:
            unregister_token(state.triage_id)
            session.close()

    def test_cancel_terminal_run_raises(self, db_factory):
        from app.api.service import cancel_triage

        repo, session = db_factory()
        try:
            state = TriageState(
                repository="acme/x", workflow_run_id="1", status=RunStatus.COMPLETED
            )
            repo.save_state(state)
            repo.commit()
            with pytest.raises(RuntimeError, match="cannot be cancelled"):
                cancel_triage(state.triage_id, repo)
        finally:
            session.close()

    def test_cancel_missing_run_raises(self, db_factory):
        from app.api.service import cancel_triage

        repo, session = db_factory()
        try:
            with pytest.raises(RuntimeError, match="not found"):
                cancel_triage("nope", repo)
        finally:
            session.close()
