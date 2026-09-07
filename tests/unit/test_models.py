"""Unit tests for the domain models."""

from app.models import (
    CheckStatus,
    CIFailure,
    ErrorType,
    Hypothesis,
    PlanStep,
    TestResult,
    TriageState,
    VerificationLevel,
    VerificationReport,
)


def test_triage_state_roundtrip_and_digest():
    s = TriageState(repository="acme/api", workflow_run_id="1842")
    s.add_hypothesis(Hypothesis(description="race condition", confidence=0.8))
    s.select_hypothesis(s.hypotheses[0].id)
    s.add_step(PlanStep(description="reproduce", action="run_test"))
    s.mark_step_completed(s.plan[0].id)
    s.failure = CIFailure(
        message="boom", test_name="test_refund", error_type=ErrorType.DETERMINISTIC_CODE
    )

    d = s.as_digest()
    assert d["repository"] == "acme/api"
    assert d["selected_hypothesis"] == s.hypotheses[0].id
    assert d["completed_steps"] == [s.plan[0].id]
    assert len(s.hypotheses) == 1

    # Pydantic serialization roundtrip (persistence/resume)
    serialized = s.model_dump_json()
    restored = TriageState.model_validate_json(serialized)
    assert restored == s


def test_triage_state_cost_accounting():
    s = TriageState()
    s.record_cost(tokens_in=1000, tokens_out=100, in_rate=0.005, out_rate=0.015)
    assert s.tokens_in == 1000
    assert s.tokens_out == 100
    assert abs(s.estimated_cost - (1000 / 1000 * 0.005 + 100 / 1000 * 0.015)) < 1e-6


def test_verification_report_all_critical_pass():
    report = VerificationReport()
    for level in VerificationLevel:
        if level in {
            VerificationLevel.ORIGINAL,
            VerificationLevel.RELATED,
            VerificationLevel.FULL,
            VerificationLevel.LINT,
            VerificationLevel.TYPECHECK,
        }:
            report.results.append(TestResult(level=level, status=CheckStatus.PASS))
    assert report.all_critical_pass is True

    report.results[0].status = CheckStatus.FAIL
    assert report.all_critical_pass is False


def test_cifailure_defaults():
    f = CIFailure()
    assert f.error_type == ErrorType.UNKNOWN
    assert f.raw_logs == ""
