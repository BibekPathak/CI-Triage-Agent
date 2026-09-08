"""Tests for the DB persistence layer."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import Base
from app.db.models import EventModel, TriageRunModel
from app.db.repository import (
    TriageRepository,
    event_to_row,
    row_to_event,
    row_to_state,
    state_to_row,
)
from app.models.domain import RunStatus
from app.models.state import TriageState
from app.observability.events import RunEvent

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """In-memory SQLite session for tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def repo(db_session: Session):
    return TriageRepository(db_session)


def _make_state(triage_id: str = "abc123") -> TriageState:
    return TriageState(
        triage_id=triage_id,
        repository="owner/repo",
        workflow_run_id="42",
        status=RunStatus.COMPLETED,
        root_cause="SyntaxError in foo.py",
        confidence=0.95,
    )


def _make_event(run_id: str = "abc123", step: str = "diagnose") -> RunEvent:
    return RunEvent(
        run_id=run_id,
        step=step,
        phase="diagnose",
        reasoning_summary="Found the bug",
        decision="Apply fix",
        evidence="Traceback shows ...",
        tool="run_command",
        duration_ms=120,
        arguments={"command": "python -m pytest"},
        result={"exit_code": 1},
    )


# ------------------------------------------------------------------
# Serialization roundtrip
# ------------------------------------------------------------------

class TestSerialization:
    def test_state_roundtrip(self):
        state = _make_state()
        row = state_to_row(state)
        recovered = row_to_state(row)
        assert recovered.triage_id == state.triage_id
        assert recovered.repository == state.repository
        assert recovered.status == state.status
        assert recovered.root_cause == state.root_cause
        assert recovered.confidence == state.confidence

    def test_event_roundtrip(self):
        event = _make_event()
        row = event_to_row(event)
        recovered = row_to_event(row)
        assert recovered.event_id == event.event_id
        assert recovered.step == event.step
        assert recovered.arguments == event.arguments
        assert recovered.result == event.result


# ------------------------------------------------------------------
# TriageRepository: runs
# ------------------------------------------------------------------

class TestRepositoryRuns:
    def test_save_and_get(self, repo: TriageRepository):
        state = _make_state()
        repo.save_state(state)
        loaded = repo.get_state("abc123")
        assert loaded is not None
        assert loaded.repository == "owner/repo"

    def test_get_nonexistent(self, repo: TriageRepository):
        assert repo.get_state("nope") is None

    def test_update_existing(self, repo: TriageRepository):
        state = _make_state()
        repo.save_state(state)

        state.root_cause = "Updated root cause"
        state.iterations = 5
        repo.save_state(state)

        loaded = repo.get_state("abc123")
        assert loaded is not None
        assert loaded.root_cause == "Updated root cause"
        assert loaded.iterations == 5

    def test_list_runs(self, repo: TriageRepository):
        repo.save_state(_make_state("run1"))
        repo.save_state(_make_state("run2"))
        runs = repo.list_runs()
        assert len(runs) == 2

    def test_list_filter_by_status(self, repo: TriageRepository):
        s1 = _make_state("run1")
        s1.status = RunStatus.COMPLETED
        s2 = _make_state("run2")
        s2.status = RunStatus.FAILED
        repo.save_state(s1)
        repo.save_state(s2)

        completed = repo.list_runs(status="completed")
        assert len(completed) == 1

    def test_list_filter_by_repo(self, repo: TriageRepository):
        s1 = _make_state("run1")
        s1.repository = "a/b"
        s2 = _make_state("run2")
        s2.repository = "c/d"
        repo.save_state(s1)
        repo.save_state(s2)

        ab = repo.list_runs(repository="a/b")
        assert len(ab) == 1

    def test_delete_run(self, repo: TriageRepository):
        repo.save_state(_make_state())
        assert repo.delete_run("abc123")
        repo._s.flush()  # ensure delete is processed
        assert repo.get_state("abc123") is None

    def test_delete_nonexistent(self, repo: TriageRepository):
        assert not repo.delete_run("nope")


# ------------------------------------------------------------------
# TriageRepository: events
# ------------------------------------------------------------------

class TestRepositoryEvents:
    def test_save_and_get_events(self, repo: TriageRepository):
        e1 = _make_event(step="diagnose")
        e2 = _make_event(step="reproduce")
        repo.save_events([e1, e2])

        events = repo.get_events("abc123")
        assert len(events) == 2
        assert events[0].step == "diagnose"
        assert events[1].step == "reproduce"

    def test_events_ordered_by_timestamp(self, repo: TriageRepository):
        e1 = _make_event(step="first")
        e1.timestamp = e1.timestamp.replace(second=1)
        e2 = _make_event(step="second")
        e2.timestamp = e2.timestamp.replace(second=2)
        repo.save_events([e2, e1])  # insert out of order

        events = repo.get_events("abc123")
        assert events[0].step == "first"
        assert events[1].step == "second"

    def test_delete_run_cascades_events(self, repo: TriageRepository):
        repo.save_state(_make_state())
        repo.save_events([_make_event(), _make_event(step="other")])
        repo._s.flush()

        repo.delete_run("abc123")
        repo._s.flush()
        assert repo.get_events("abc123") == []


# ------------------------------------------------------------------
# ORM model repr
# ------------------------------------------------------------------

class TestModelRepr:
    def test_triage_run_repr(self):
        row = TriageRunModel(triage_id="x", status="completed")
        assert "x" in repr(row)
        assert "completed" in repr(row)

    def test_event_repr(self):
        row = EventModel(event_id="e1", step="diagnose")
        assert "e1" in repr(row)
        assert "diagnose" in repr(row)
