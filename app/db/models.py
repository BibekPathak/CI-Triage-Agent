"""SQLAlchemy ORM models for persistence.

Two tables:
- ``triage_runs``: one row per triage run; the full ``TriageState`` is stored
  as a JSON blob (``state_json``) with key fields extracted for querying.
- ``events``: execution trace events tied to a run.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.engine import Base


class TriageRunModel(Base):
    """One row per triage run."""

    __tablename__ = "triage_runs"

    # Primary key.
    triage_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Extracted fields for fast queries / filtering.
    repository: Mapped[str] = mapped_column(String(256), default="", index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="initializing", index=True)

    # Outcome fields (nullable).
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_pr_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    final_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Telemetry.
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)

    # Full state as JSON blob.
    state_json: Mapped[str] = mapped_column(Text, default="{}")

    # Timestamps.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<TriageRun {self.triage_id} status={self.status}>"


class EventModel(Base):
    """One row per execution trace event."""

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    step: Mapped[str] = mapped_column(String(128), default="")
    phase: Mapped[str] = mapped_column(String(64), default="")
    reasoning_summary: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Structured fields as JSON.
    arguments_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self) -> str:
        return f"<Event {self.event_id} step={self.step}>"
