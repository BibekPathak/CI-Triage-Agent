"""Prompt templates and structured output schemas for the agent."""

from app.agent.prompts.schemas import (
    Diagnosis,
    PatchProposal,
    Plan,
    PullRequest,
    RootCauseAnalysis,
    VerificationDecision,
)
from app.agent.prompts.templates import (
    SYSTEM_FAILURE_ANALYSIS,
    SYSTEM_PATCH,
    SYSTEM_PLANNING,
    SYSTEM_PR,
    SYSTEM_ROOT_CAUSE,
    SYSTEM_VERIFY,
    user_failure_analysis,
    user_patch,
    user_planning,
    user_pr,
    user_root_cause,
    user_verify,
)

__all__ = [
    "Diagnosis",
    "PatchProposal",
    "Plan",
    "PullRequest",
    "RootCauseAnalysis",
    "VerificationDecision",
    "SYSTEM_FAILURE_ANALYSIS",
    "SYSTEM_PLANNING",
    "SYSTEM_ROOT_CAUSE",
    "SYSTEM_PATCH",
    "SYSTEM_VERIFY",
    "SYSTEM_PR",
    "user_failure_analysis",
    "user_planning",
    "user_root_cause",
    "user_patch",
    "user_verify",
    "user_pr",
]
