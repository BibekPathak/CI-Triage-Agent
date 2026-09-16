"""Tests for the evaluation framework."""

from __future__ import annotations

from app.evaluation.metrics import aggregate
from app.evaluation.models import BenchmarkScenario, ScenarioResult
from app.evaluation.runner import expected_set, run_benchmark
from app.evaluation.scenarios import all_scenarios, write_repo_files


def _result(succeeded: bool = True, **overrides) -> ScenarioResult:
    s = BenchmarkScenario(
        name="x", repository="r/x", failing_command="pytest -q",
        expected_root_cause="abc", expected_files=["src/a.py"],
        validation_command="pytest -q", files={"src/a.py": "x"},
    )
    kwargs = dict(
        scenario=s, succeeded=succeeded, reproduced=True,
        root_cause_matched=True, patch_applied=True, original_resolved=True,
        relevant_tests_passed=True, no_regression=True,
        extra_files_changed=[], iterations=2, tool_calls=10,
        llm_calls=4, execution_time_ms=1000,
    )
    kwargs.update(overrides)
    return ScenarioResult(**kwargs)


class TestScenarios:
    def test_ten_scenarios(self):
        scenarios = all_scenarios()
        assert len(scenarios) == 10
        names = [s.name for s in scenarios]
        assert len(set(names)) == 10  # unique

    def test_scenario_shape(self):
        s = all_scenarios()[0]
        assert s.expected_files
        assert s.fix_file in s.files
        assert s.failing_command
        assert s.validation_command

    def test_write_repo_files(self, tmp_path):
        s = all_scenarios()[0]
        write_repo_files(s, str(tmp_path))
        for rel in s.files:
            assert (tmp_path / rel).exists()

    def test_expected_set(self):
        s = all_scenarios()[0]
        assert expected_set(s) == set(s.expected_files)


class TestMetrics:
    def test_empty(self):
        m = aggregate([])
        assert m["count"] == 0
        assert m["task_success_rate"] == 0.0

    def test_all_success(self):
        m = aggregate([_result(True) for _ in range(2)])
        assert m["count"] == 2
        assert m["task_success_rate"] == 1.0
        assert m["root_cause_accuracy"] == 1.0
        assert m["patch_success_rate"] == 1.0
        assert m["regression_rate"] == 0.0
        assert m["unnecessary_file_change_rate"] == 0.0
        assert m["average_iterations"] == 2.0
        assert m["average_tool_calls"] == 10.0
        assert m["average_llm_calls"] == 4.0

    def test_partial_success(self):
        ok = _result(True)
        bad = _result(False)
        m = aggregate([ok, bad])
        assert m["task_success_rate"] == 0.5

    def test_regression_and_extra_files(self):
        r = _result(
            True,
            no_regression=False,
            extra_files_changed=["secret.py"],
        )
        m = aggregate([r])
        assert m["regression_rate"] == 1.0
        assert m["unnecessary_file_change_rate"] == 1.0


class TestRunner:
    def test_run_benchmark_single(self):
        # One scenario is enough to exercise the real offline flow.
        results = run_benchmark(all_scenarios()[:1])
        assert len(results) == 1
        r = results[0]
        assert r.patch_applied is True
        assert r.original_resolved is True
        assert r.reproduced is True
        assert r.no_regression is True
        assert r.succeeded is True
        assert r.extra_files_changed == []
