"""Tests for prompt templates and structured schema parsing."""

from app.agent.prompts import (
    Diagnosis,
    Plan,
    RootCauseAnalysis,
    VerificationDecision,
    user_failure_analysis,
    user_patch,
    user_planning,
    user_root_cause,
    user_verify,
)
from app.models import CheckStatus, CIFailure, TestResult, VerificationLevel


def test_diagnosis_schema_roundtrip():
    raw = {
        "root_cause": "null deref",
        "evidence": ["log line"],
        "confidence": 0.81,
        "error_category": "deterministic_code",
        "next_action": "inspect payments.py",
        "reasoning_summary": "found null path",
    }
    diag = Diagnosis.model_validate(raw)
    assert diag.root_cause == "null deref"
    assert diag.confidence == 0.81


def test_root_cause_schema_parses_hypotheses():
    raw = {
        "hypotheses": [
            {"description": "race", "confidence": 0.7, "verification_strategy": "run test 10x"},
        ],
        "selected_hypothesis": "race",
        "confidence": 0.7,
    }
    rca = RootCauseAnalysis.model_validate(raw)
    assert rca.hypotheses[0]["description"] == "race"


def test_verify_schema_parses_verdict():
    d = VerificationDecision.model_validate({"verdict": "needs_iteration", "next_action": "retry"})
    assert d.verdict == "needs_iteration"


def test_user_failure_analysis_contains_logs_and_context():
    logs = "FAILED test_refund, AssertionError"
    ctx = "Job: test"
    out = user_failure_analysis(logs, ctx)
    assert "test_refund" in out


def test_user_planning_builds():
    out = user_planning("root cause here", "state digest")
    assert "Diagnosis" in out


def test_user_root_cause_builds():
    f = CIFailure(message="boom", test_name="test_refund")
    out = user_root_cause(str(f.model_dump()), "src/")
    assert out


def test_user_patch_contains_file_content():
    out = user_patch("src/refund.py", "return total", "timezone bug", "H1 tz")
    assert "src/refund.py" in out
    assert "return total" in out


def test_user_verify_lists_layers():
    results = [
        TestResult(level=VerificationLevel.ORIGINAL, status=CheckStatus.PASS),
        TestResult(level=VerificationLevel.FULL, status=CheckStatus.FAIL, failed=1),
    ]
    out = user_verify(results, "src/refund.py")
    assert "original" in out
    assert "full" in out


def test_plan_schema_roundtrip():
    p = Plan.model_validate({"goal": "reproduce", "steps": ["run test"], "rationale": "x"})
    assert p.steps == ["run test"]
