"""Unit tests for the shell command runner and test runner."""

from app.models.domain import CheckStatus
from app.sandbox.runners import LocalCommandRunner
from app.tools.registry import default_registry


def test_local_runner_allows_safe(tmp_path):
    runner = LocalCommandRunner()
    out = runner.run("pwd", cwd=str(tmp_path))
    assert out.exit_code == 0
    assert str(tmp_path) in out.stdout


def test_local_runner_denies_dangerous():
    runner = LocalCommandRunner()
    out = runner.run("rm -rf /")
    assert out.denied is True
    assert out.exit_code == -1


def test_local_runner_denies_unknown_command():
    runner = LocalCommandRunner()
    out = runner.run("this_binary_does_not_exist_xyz")
    assert out.denied is True
    assert "allowlist" in (out.error or "")


def test_run_tests_passing(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_pass():\n    assert True\n")
    reg = default_registry()
    res = reg.execute(
        "run_tests",
        {"command": "pytest -q test_ok.py", "cwd": str(tmp_path), "level": "related"},
    )
    tr = res.data["test_result"]
    assert tr["status"] == CheckStatus.PASS
    assert tr["passed"] == 1


def test_run_tests_failing(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_fail():\n    assert False\n")
    reg = default_registry()
    res = reg.execute(
        "run_tests",
        {"command": "pytest -q test_bad.py", "cwd": str(tmp_path), "level": "original"},
    )
    tr = res.data["test_result"]
    assert tr["status"] == CheckStatus.FAIL
    assert tr["failed"] == 1
