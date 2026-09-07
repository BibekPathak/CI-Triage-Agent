"""Observability: execution trace events.

Every meaningful agent step produces a :class:`RunEvent` capturing a concise
``reasoning_summary``, ``decision``, ``evidence``, ``action`` and ``result``.
Hidden chain-of-thought is never stored; only condensed, auditable traces are.

Phase 4 ships an in-memory :class:`EventRecorder`; Phase 6/9 will flush events
and metrics to the persistence layer and expose them via the API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step: str = ""  # e.g. "diagnose_failure", "reproduce", "apply_patch"
    phase: str = ""
    reasoning_summary: str = ""
    decision: str = ""
    evidence: str = ""
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventRecorder:
    """Collects :class:`RunEvent` objects, optionally bound to a run id."""

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self._events: list[RunEvent] = []

    def bind(self, run_id: str) -> None:
        self.run_id = run_id

    def record(self, event: RunEvent) -> RunEvent:
        if not event.run_id:
            event.run_id = self.run_id
        self._events.append(event)
        return event

    def drain(self) -> list[RunEvent]:
        events, self._events = self._events, []
        return events

    @property
    def events(self) -> list[RunEvent]:
        return list(self._events)

    def as_trace(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self._events]
