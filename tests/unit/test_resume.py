"""Tests for resume-after-restart at the approval boundary."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.service import is_resumable, resume_triage
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


def _approval_state() -> TriageState:
    state = TriageState(
        repository="acme/payments",
        workflow_run_id="42",
        status=RunStatus.AWAITING_APPROVAL,
        approval_required=True,
        diff_signed_off=True,
    )
    state.candidate_patch = (
        "--- a/src/payment.py\n+++ b/src/payment.py\n"
        "@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n"
        "+    return a + b\n"
    )
    state.proposed_pr_title = "fix: payment"
    return state


def _completed_no_patch_state() -> TriageState:
    state = TriageState(repository="acme/payments", workflow_run_id="7")
    state.status = RunStatus.COMPLETED
    state.candidate_patch = None
    state.diff_signed_off = True
    return state


class TestIsResumable:
    def test_approval_state_is_resumable(self):
        assert is_resumable(_approval_state()) is True

    def test_completed_no_patch_not_resumable(self):
        assert is_resumable(_completed_no_patch_state()) is False

    def test_patch_missing_not_resumable(self):
        s = _approval_state()
        s.candidate_patch = None
        assert is_resumable(s) is False

    def test_not_signed_off_not_resumable(self):
        s = _approval_state()
        s.diff_signed_off = False
        assert is_resumable(s) is False


class TestResumeTriage:
    def test_resume_missing_run_raises(self, db_factory):
        repo, session = db_factory()
        try:
            with pytest.raises(RuntimeError, match="not found"):
                resume_triage("nope", repo)
        finally:
            session.close()

    def test_resume_non_resumable_raises(self, db_factory):
        repo, session = db_factory()
        try:
            state = _completed_no_patch_state()
            repo.save_state(state)
            repo.commit()
            with pytest.raises(RuntimeError, match="not resumable"):
                resume_triage(state.triage_id, repo)
        finally:
            session.close()

    def test_resume_returns_persisted_state(self, db_factory):
        repo, session = db_factory()
        try:
            state = _approval_state()
            repo.save_state(state)
            repo.commit()
            loaded = resume_triage(state.triage_id, repo)
            assert loaded.status == RunStatus.AWAITING_APPROVAL
            assert loaded.candidate_patch
        finally:
            session.close()


class TestResumeRestartFlow:
    """Simulates a process restart: persist -> fresh session -> resume -> approve."""

    def test_resume_from_reloaded_state(self, db_factory):
        repo1, session1 = db_factory()
        try:
            state = _approval_state()
            repo1.save_state(state)
            repo1.commit()
        finally:
            session1.close()

        # Restart: a fresh session/engine bound to the SAME in-memory db.
        repo2, session2 = db_factory()
        try:
            loaded = repo2.get_state(state.triage_id)
            assert loaded is not None
            assert is_resumable(loaded)
            assert loaded.proposed_pr_title == "fix: payment"
        finally:
            session2.close()
