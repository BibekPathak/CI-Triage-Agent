"""The persistent, typed agent state.

``TriageState`` holds everything the agent knows across the run so that it can
be serialized to the database and resumed after a crash. The agent loop mutates
a single instance of this object rather than relying on conversation history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.models.domain import (
    ApprovalStatus,
    CIFailure,
    Hypothesis,
    PlanStatus,
    PlanStep,
    RunStatus,
    TestResult,
    VerificationReport,
)


class TriageState(BaseModel):
    """The full state of one triage run."""

    triage_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    repository: str = ""
    workflow_run_id: str = ""

    status: RunStatus = RunStatus.INITIALIZING

    # ----- CI context -----
    failure: CIFailure = Field(default_factory=CIFailure)
    ci_logs: str | None = None

    # ----- Repository context -----
    repository_context: dict = Field(default_factory=dict)

    # ----- Hypotheses -----
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    selected_hypothesis: str | None = None

    # ----- Plan -----
    plan: list[PlanStep] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)

    # ----- Workspace / patch -----
    workspace_path: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    candidate_patch: str | None = None
    diff_signed_off: bool = False

    # ----- Verification -----
    verification: VerificationReport = Field(default_factory=VerificationReport)
    test_results: list[TestResult] = Field(default_factory=list)

    # ----- Outcome -----
    confidence: float | None = None
    root_cause: str | None = None
    risk_assessment: str | None = None
    proposed_pr_title: str | None = None
    proposed_pr_body: str | None = None

    # ----- Approval -----
    approval_required: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED

    # ----- Telemetry / accounting -----
    iterations: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0
    last_error: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    final_result: str | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def record_cost(self, tokens_in: int, tokens_out: int, in_rate: float, out_rate: float) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.estimated_cost += (tokens_in / 1000) * in_rate + (tokens_out / 1000) * out_rate

    def add_hypothesis(self, h: Hypothesis) -> None:
        self.hypotheses.append(h)

    def select_hypothesis(self, hypothesis_id: str) -> None:
        self.selected_hypothesis = hypothesis_id

    def add_step(self, step: PlanStep) -> None:
        self.plan.append(step)

    def mark_step_completed(self, step_id: str) -> None:
        if step_id not in self.completed_steps:
            self.completed_steps.append(step_id)
        for step in self.plan:
            if step.id == step_id:
                step.status = PlanStatus.COMPLETED

    def as_digest(self) -> dict:
        """A compact, human/LLM-friendly snapshot of state for context."""
        return {
            "triage_id": self.triage_id,
            "repository": self.repository,
            "workflow_run_id": self.workflow_run_id,
            "status": self.status.value,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "hypotheses": [h.description for h in self.hypotheses],
            "selected_hypothesis": self.selected_hypothesis,
            "completed_steps": self.completed_steps,
            "changed_files": self.changed_files,
            "approval_status": self.approval_status.value,
        }
