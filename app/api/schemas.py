"""API request/response schemas.

Pydantic models for the HTTP layer, separate from the domain models.
These define the public contract for the REST API.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Requests
# ------------------------------------------------------------------

class TriageRunRequest(BaseModel):
    """POST /api/v1/triage — trigger a new triage run."""

    repository: str = Field(description="GitHub repo (owner/repo)")
    workflow_run_id: str = Field(description="GitHub Actions workflow run ID")


class ApprovalRequest(BaseModel):
    """POST /api/v1/triage/{triage_id}/approve — approve a pending fix."""

    approve: bool = Field(description="True to approve, false to reject")
    comment: str = Field(default="", description="Optional reason or note")


# ------------------------------------------------------------------
# Responses
# ------------------------------------------------------------------

class TriageRunResponse(BaseModel):
    """Response for a triage run (created or retrieved)."""

    triage_id: str
    repository: str
    workflow_run_id: str
    status: str
    root_cause: str | None = None
    confidence: float | None = None
    proposed_pr_title: str | None = None
    approval_status: str = "not_required"
    created_at: datetime
    updated_at: datetime


class TriageRunDetailResponse(TriageRunResponse):
    """Extended response with full state details."""

    ci_logs: str | None = None
    hypotheses: list[dict] = Field(default_factory=list)
    plan: list[dict] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    candidate_patch: str | None = None
    verification: dict | None = None
    iterations: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0
    last_error: str | None = None
    final_result: str | None = None


class EventResponse(BaseModel):
    """A single execution trace event."""

    event_id: str
    timestamp: datetime
    step: str
    phase: str
    reasoning_summary: str
    decision: str
    tool: str | None = None
    duration_ms: int = 0


class HealthResponse(BaseModel):
    """GET /api/v1/health — liveness / readiness."""

    status: str = "ok"
    version: str = "0.1.0"
    database: str = "ok"


class MetricsResponse(BaseModel):
    """GET /api/v1/metrics — current metrics snapshot."""

    counters: dict[str, float] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    histograms: dict[str, dict] = Field(default_factory=dict)
    timestamp: float = 0.0


class ErrorResponse(BaseModel):
    """Generic error response."""

    error: str
    detail: str = ""
