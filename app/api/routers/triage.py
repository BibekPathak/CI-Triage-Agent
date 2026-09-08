"""Triage router — CRUD + trigger for triage runs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_metrics, get_repository
from app.api.schemas import (
    ApprovalRequest,
    EventResponse,
    TriageRunDetailResponse,
    TriageRunRequest,
    TriageRunResponse,
)
from app.db.repository import TriageRepository
from app.models.state import TriageState
from app.observability.metrics import Metrics

router = APIRouter(prefix="/api/v1/triage", tags=["triage"])


def _state_to_response(state: TriageState) -> TriageRunResponse:

    return TriageRunResponse(
        triage_id=state.triage_id,
        repository=state.repository,
        workflow_run_id=state.workflow_run_id,
        status=state.status.value,
        root_cause=state.root_cause,
        confidence=state.confidence,
        proposed_pr_title=state.proposed_pr_title,
        approval_status=state.approval_status.value,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


@router.post("", response_model=TriageRunResponse, status_code=201)
def create_triage(
    req: TriageRunRequest,
    repo: Annotated[TriageRepository, Depends(get_repository)],
    m: Annotated[Metrics, Depends(get_metrics)],
) -> TriageRunResponse:
    """Create and kick off a new triage run.

    In production this would trigger the orchestrator asynchronously; for
    now it persists a stub state so the status / list endpoints work.
    """

    from app.models.domain import RunStatus
    from app.models.state import TriageState

    state = TriageState(
        repository=req.repository,
        workflow_run_id=req.workflow_run_id,
        status=RunStatus.INITIALIZING,
    )
    repo.save_state(state)
    m.counter("api_triggers").inc()
    return _state_to_response(state)


@router.get("", response_model=list[TriageRunResponse])
def list_triages(
    repo: Annotated[TriageRepository, Depends(get_repository)],
    repository: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TriageRunResponse]:
    """List triage runs with optional filters."""
    rows = repo.list_runs(repository=repository, status=status, limit=limit, offset=offset)
    return [
        TriageRunResponse(
            triage_id=r.triage_id,
            repository=r.repository,
            workflow_run_id=r.workflow_run_id,
            status=r.status,
            root_cause=r.root_cause,
            confidence=r.confidence,
            proposed_pr_title=r.proposed_pr_title,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/{triage_id}", response_model=TriageRunDetailResponse)
def get_triage(
    triage_id: str,
    repo: Annotated[TriageRepository, Depends(get_repository)],
) -> TriageRunDetailResponse:
    """Retrieve full details for a single triage run."""
    state = repo.get_state(triage_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Triage run not found")

    return TriageRunDetailResponse(
        triage_id=state.triage_id,
        repository=state.repository,
        workflow_run_id=state.workflow_run_id,
        status=state.status.value,
        root_cause=state.root_cause,
        confidence=state.confidence,
        proposed_pr_title=state.proposed_pr_title,
        approval_status=state.approval_status.value,
        created_at=state.created_at,
        updated_at=state.updated_at,
        ci_logs=state.ci_logs,
        hypotheses=[h.model_dump() for h in state.hypotheses],
        plan=[s.model_dump() for s in state.plan],
        changed_files=state.changed_files,
        candidate_patch=state.candidate_patch,
        verification=state.verification.model_dump(),
        iterations=state.iterations,
        tool_calls=state.tool_calls,
        llm_calls=state.llm_calls,
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
        estimated_cost=state.estimated_cost,
        last_error=state.last_error,
        final_result=state.final_result,
    )


@router.post("/{triage_id}/approve", response_model=TriageRunResponse)
def approve_triage(
    triage_id: str,
    req: ApprovalRequest,
    repo: Annotated[TriageRepository, Depends(get_repository)],
    m: Annotated[Metrics, Depends(get_metrics)],
) -> TriageRunResponse:
    """Approve or reject a pending fix."""
    from app.models.domain import ApprovalStatus

    state = repo.get_state(triage_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Triage run not found")

    if req.approve:
        state.approval_status = ApprovalStatus.APPROVED
        state.diff_signed_off = True
    else:
        state.approval_status = ApprovalStatus.REJECTED

    state.touch()
    repo.save_state(state)
    m.counter("api_approvals").inc()
    return _state_to_response(state)


@router.get("/{triage_id}/events", response_model=list[EventResponse])
def get_triage_events(
    triage_id: str,
    repo: Annotated[TriageRepository, Depends(get_repository)],
    limit: int = 200,
) -> list[EventResponse]:
    """Fetch execution trace events for a triage run."""
    state = repo.get_state(triage_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Triage run not found")

    events = repo.get_events(triage_id, limit=limit)
    return [
        EventResponse(
            event_id=e.event_id,
            timestamp=e.timestamp,
            step=e.step,
            phase=e.phase,
            reasoning_summary=e.reasoning_summary,
            decision=e.decision,
            tool=e.tool,
            duration_ms=e.duration_ms,
        )
        for e in events
    ]


@router.delete("/{triage_id}", status_code=204)
def delete_triage(
    triage_id: str,
    repo: Annotated[TriageRepository, Depends(get_repository)],
) -> None:
    """Delete a triage run and its events."""
    if not repo.delete_run(triage_id):
        raise HTTPException(status_code=404, detail="Triage run not found")
