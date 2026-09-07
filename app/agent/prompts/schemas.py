"""Structured output schemas the agent requires from the LLM.

Each corresponds to a distinct reasoning stage. The LLM returns one of these
JSON objects; the orchestrator validates them and turns them into typed domain
models / tool calls.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Diagnosis(BaseModel):
    """Structured output of failure analysis."""

    root_cause: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    error_category: str = "unknown"
    next_action: str = ""
    reasoning_summary: str = ""


class Plan(BaseModel):
    """Structured output of planning."""

    goal: str = ""
    steps: list[str] = Field(default_factory=list)
    rationale: str = ""


class RootCauseAnalysis(BaseModel):
    """Structured output of root cause analysis (multiple hypotheses)."""

    hypotheses: list[dict] = Field(default_factory=list)
    selected_hypothesis: str = ""
    confidence: float = 0.0
    reasoning_summary: str = ""


class PatchProposal(BaseModel):
    """Structured output of patch generation."""

    file: str = ""
    original: str = ""
    replacement: str = ""
    explanation: str = ""
    risk: str = "low"


class VerificationDecision(BaseModel):
    """Structured output of test-result evaluation."""

    verdict: str = "needs_iteration"  # success | needs_iteration | give_up
    next_action: str = ""
    reasoning_summary: str = ""


class PullRequest(BaseModel):
    """Structured output of PR generation (after approval)."""

    title: str = ""
    body: str = ""
    labeled_agent: bool = True
