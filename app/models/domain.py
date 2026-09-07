"""Core domain models for the CI Triage Agent.

These are the typed data structures that flow through the agent loop.
Keep them as plain Pydantic models with no I/O so they are trivially
testable and serializable to/from the persistence layer.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class ErrorType(StrEnum):
    """Classification of the underlying cause of a CI failure."""

    DETERMINISTIC_CODE = "deterministic_code"
    FLAKY_TEST = "flaky_test"
    DEPENDENCY = "dependency"
    INFRASTRUCTURE = "infrastructure"
    ENVIRONMENT_CONFIG = "environment_config"
    TIMEOUT = "timeout"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


class VerificationLevel(StrEnum):
    """Progressive layers of verification for a candidate fix."""

    ORIGINAL = "original"  # Level 1: the originally failing test
    RELATED = "related"  # Level 2: related tests
    FULL = "full"  # Level 3: full test suite
    LINT = "lint"  # Level 4: static lint checks
    TYPECHECK = "typecheck"  # Level 5: static type checking
    BUILD = "build"  # Level 5+: build/package checks


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    PENDING = "PENDING"


class PlanStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ActionClass(StrEnum):
    """Security classification of a tool action (see agent/policies.py)."""

    READ_ONLY = "read_only"
    SANDBOX_WRITE = "sandbox_write"
    REMOTE_WRITE = "remote_write"
    DESTRUCTIVE = "destructive"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    AUTO_APPROVED = "auto_approved"


class RunStatus(StrEnum):
    INITIALIZING = "initializing"
    COLLECTING = "collecting"
    DIAGNOSING = "diagnosing"
    PLANNING = "planning"
    REPRODUCING = "reproducing"
    PATCHING = "patching"
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------- #
# CI failure model
# --------------------------------------------------------------------------- #
class CIFailure(BaseModel):
    """A normalized, structured representation of a CI failure."""

    workflow: str = ""
    job: str = ""
    step: str = ""
    error_type: ErrorType = ErrorType.UNKNOWN
    message: str = ""
    stack_trace: str | None = None
    file: str | None = None
    line: int | None = None
    test_name: str | None = None
    exit_code: int | None = None
    raw_logs: str = ""


# --------------------------------------------------------------------------- #
# Hypotheses
# --------------------------------------------------------------------------- #
class Hypothesis(BaseModel):
    """A candidate explanation for the failure with evidence and a test plan."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    verification_strategy: str = ""
    verified: bool | None = None


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
class PlanStep(BaseModel):
    """A single step in the agent's debugging plan."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    action: str = ""  # terse action label, e.g. "run_targeted_test"
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: PlanStatus = PlanStatus.PENDING
    result: Any | None = None


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
class TestResult(BaseModel):
    """Result of running a test command / suite layer."""

    __test__ = False  # exclude from pytest collection (name collision)

    level: VerificationLevel
    command: str = ""
    status: CheckStatus = CheckStatus.PENDING
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error: str | None = None
    duration_ms: int = 0
    output: str = ""


class VerificationReport(BaseModel):
    """Aggregated verification results keyed by verification level."""

    results: list[TestResult] = Field(default_factory=list)

    def status_for(self, level: VerificationLevel) -> CheckStatus:
        # Most recent result for a level (so post-patch verification wins over
        # the earlier reproduce-only run of the same layer).
        for r in reversed(self.results):
            if r.level == level:
                return r.status
        return CheckStatus.SKIPPED

    @property
    def all_critical_pass(self) -> bool:
        """True if original+related+full+lint+typecheck all pass."""
        critical = {
            VerificationLevel.ORIGINAL,
            VerificationLevel.RELATED,
            VerificationLevel.FULL,
            VerificationLevel.LINT,
            VerificationLevel.TYPECHECK,
        }
        return all(self.status_for(level) == CheckStatus.PASS for level in critical)


# --------------------------------------------------------------------------- #
# Tool result
# --------------------------------------------------------------------------- #
class ToolResult(BaseModel):
    """Structured output of any tool invocation."""

    tool: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
