"""Repository layer: CRUD operations for triage runs and events.

Provides a clean API over the ORM models, handling serialization of
``TriageState`` ↔ JSON and ``RunEvent`` ↔ JSON.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import EventModel, TriageRunModel
from app.models.state import TriageState
from app.observability.events import RunEvent

# ------------------------------------------------------------------
# Serialization helpers
# ------------------------------------------------------------------

def state_to_row(state: TriageState) -> TriageRunModel:
    """Convert a ``TriageState`` to an ORM row (for insert/update)."""
    return TriageRunModel(
        triage_id=state.triage_id,
        repository=state.repository,
        workflow_run_id=state.workflow_run_id,
        status=state.status.value,
        root_cause=state.root_cause,
        confidence=state.confidence,
        proposed_pr_title=state.proposed_pr_title,
        final_result=state.final_result,
        iterations=state.iterations,
        tool_calls=state.tool_calls,
        llm_calls=state.llm_calls,
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
        estimated_cost=state.estimated_cost,
        state_json=state.model_dump_json(),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def row_to_state(row: TriageRunModel) -> TriageState:
    """Reconstruct a ``TriageState`` from a DB row."""
    return TriageState.model_validate_json(row.state_json)


def event_to_row(event: RunEvent) -> EventModel:
    """Convert a ``RunEvent`` to an ORM row."""
    return EventModel(
        event_id=event.event_id,
        run_id=event.run_id,
        timestamp=event.timestamp,
        step=event.step,
        phase=event.phase,
        reasoning_summary=event.reasoning_summary,
        decision=event.decision,
        evidence=event.evidence,
        tool=event.tool,
        duration_ms=event.duration_ms,
        arguments_json=json.dumps(event.arguments, default=str),
        result_json=json.dumps(event.result, default=str),
        metadata_json=json.dumps(event.metadata, default=str),
    )


def row_to_event(row: EventModel) -> RunEvent:
    """Reconstruct a ``RunEvent`` from a DB row."""
    return RunEvent(
        event_id=row.event_id,
        run_id=row.run_id,
        timestamp=row.timestamp,
        step=row.step,
        phase=row.phase,
        reasoning_summary=row.reasoning_summary,
        decision=row.decision,
        evidence=row.evidence,
        tool=row.tool,
        duration_ms=row.duration_ms,
        arguments=json.loads(row.arguments_json) if row.arguments_json else {},
        result=json.loads(row.result_json) if row.result_json else {},
        metadata=json.loads(row.metadata_json) if row.metadata_json else {},
    )


# ------------------------------------------------------------------
# Repository
# ------------------------------------------------------------------

class TriageRepository:
    """CRUD operations for triage runs and events."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def commit(self) -> None:
        """Commit the current transaction."""
        self._s.commit()

    # ----- triage runs -----

    def save_state(self, state: TriageState) -> TriageRunModel:
        """Insert or update a triage run from a ``TriageState``."""
        existing = self._s.get(TriageRunModel, state.triage_id)
        if existing is not None:
            # Update existing row.
            new_row = state_to_row(state)
            for col_name in TriageRunModel.__table__.columns.keys():  # type: ignore[union-attr]  # noqa: SIM118
                if col_name != "triage_id":
                    setattr(existing, col_name, getattr(new_row, col_name))
            return existing

        row = state_to_row(state)
        self._s.add(row)
        self._s.flush()
        return row

    def get_state(self, triage_id: str) -> TriageState | None:
        """Load a ``TriageState`` by ID."""
        row = self._s.get(TriageRunModel, triage_id)
        if row is None:
            return None
        return row_to_state(row)

    def list_runs(
        self,
        repository: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TriageRunModel]:
        """Query triage runs with optional filters."""
        stmt = select(TriageRunModel).order_by(desc(TriageRunModel.created_at))
        if repository:
            stmt = stmt.where(TriageRunModel.repository == repository)
        if status:
            stmt = stmt.where(TriageRunModel.status == status)
        stmt = stmt.offset(offset).limit(limit)
        return list(self._s.execute(stmt).scalars().all())

    def delete_run(self, triage_id: str) -> bool:
        """Delete a triage run and its events. Returns True if found."""
        row = self._s.get(TriageRunModel, triage_id)
        if row is None:
            return False
        # Delete associated events first.
        events = self._s.execute(
            select(EventModel).where(EventModel.run_id == triage_id)
        ).scalars().all()
        for ev in events:
            self._s.delete(ev)
        self._s.delete(row)
        return True

    # ----- events -----

    def save_event(self, event: RunEvent) -> EventModel:
        """Insert a single event."""
        row = event_to_row(event)
        self._s.add(row)
        self._s.flush()
        return row

    def save_events(self, events: Sequence[RunEvent]) -> list[EventModel]:
        """Bulk-insert events."""
        rows = [event_to_row(e) for e in events]
        self._s.add_all(rows)
        self._s.flush()
        return rows

    def get_events(self, run_id: str, limit: int = 200) -> list[RunEvent]:
        """Fetch events for a run, ordered by timestamp."""
        stmt = (
            select(EventModel)
            .where(EventModel.run_id == run_id)
            .order_by(EventModel.timestamp)
            .limit(limit)
        )
        rows = self._s.execute(stmt).scalars().all()
        return [row_to_event(r) for r in rows]
