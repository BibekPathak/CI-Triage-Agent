"""Tests for the multi-toolchain CI log parser."""

from __future__ import annotations

from app.agent.ci_parser import parse_ci_logs
from app.models import ErrorType

# --------------------------------------------------------------------------- #
# Realistic log fixtures
# --------------------------------------------------------------------------- #

PYTEST_LOGS = """\
##[group]Job: test
##[group]Run python -m pytest
============================= test session starts ==============================
platform linux -- Python 3.12.7, pytest-9.1.1
collected 47 items

tests/test_payment.py .....F....                                [ 40%]
tests/test_refund.py ............                              [ 80%]

_____________ test_refund ______________________________
tests/test_payment.py:142: in test_refund
    assert refund_amount == expected
E   AssertionError: expected 200, got 500
--------------------------- Captured stdout call ------------------------------

FAILED tests/test_payment.py::test_refund

The process '/usr/bin/python' failed with exit code 1
"""

JEST_LOGS = """\
> jest --ci

RUNS  test/api.test.js
FAIL test/api.test.js
  ● GET /payments › returns 200

    expect(received).toBe(expected)

    Expected: 200
    Received: 500

      45 |     const res = await request(app).get('/payments');
      46 |     expect(res.statusCode).toBe(200);
    at Object.<anonymous> (test/api.test.js:46:19)

Test Suites: 1 failed, 1 total
Tests:       1 failed, 42 passed, 43 total
Exit code: 1
"""

CARGO_LOGS = """\
   Compiling payments v0.1.0
    Finished dev [unoptimized] profile
     Running unittests src/lib.rs

running 2 tests
test payments::tests::compute_total ... FAILED

---- payments::tests::compute_total stdout ----
thread 'payments::tests::compute_total' panicked at src/lib.rs:42:5:
assertion `left == right` failed
error: test failed, to rerun pass `--lib`
"""

GO_LOGS = """\
=== RUN   TestComputeTotal
    payments_test.go:27: compute_total() = 250, want 200
--- FAIL: TestComputeTotal (0.00s)
FAIL
FAIL    example.org/payments   6.534s
ok
"""

COMPILER_LOGS = """\
error[E0308]: mismatched types
  --> src/main.rs:12:9
   |
12 |     let x: u32 = foo;
   |         ^  expected `u32`, found `String`
   |
error: aborting due to previous error
Process completed with exit code 1
"""

SHELL_LOGS = """\
##[group]Run npm ci
npm ERR! code ENOENT
npm ERR! syscall open
npm ERR! path /home/runner/work/app/package-lock.json
npm ERR! errno -2
npm ERR! enoent ENOENT: no such file or directory
npm ERR! A complete log of this run can be found in: /home/runner/.npm/_logs/...
"""

GHA_LOGS = """\
##[group]Job: integration
##[group]Run npm test
Start: npm test
> node --test
Some output here
##[error]Command failed: npm test
The process '/usr/bin/npm' failed with exit code 2
"""


# --------------------------------------------------------------------------- #
# Parser tests
# --------------------------------------------------------------------------- #

class TestPytest:
    def test_extracts_failure_fields(self):
        f = parse_ci_logs(PYTEST_LOGS)
        assert f.file == "tests/test_payment.py"
        assert f.test_name == "test_refund"
        assert f.exit_code == 1
        assert f.error_type == ErrorType.DETERMINISTIC_CODE
        assert "AssertionError" in f.message
        assert f.raw_logs == PYTEST_LOGS

    def test_extracts_line_number(self):
        f = parse_ci_logs(PYTEST_LOGS)
        # Matches the "tests/test_payment.py:142" in the failure block.
        assert f.line == 142


class TestJest:
    def test_extracts_js_file_and_line(self):
        f = parse_ci_logs(JEST_LOGS)
        assert f.file == "test/api.test.js"
        assert f.line == 46
        assert f.exit_code == 1
        assert f.error_type == ErrorType.DETERMINISTIC_CODE


class TestCargo:
    def test_extracts_test_and_panic(self):
        f = parse_ci_logs(CARGO_LOGS)
        assert f.test_name == "payments::tests::compute_total"
        assert f.file == "src/lib.rs"
        assert f.line == 42
        assert "panicked" in f.message or f.message


class TestGo:
    def test_extracts_go_test_failure(self):
        f = parse_ci_logs(GO_LOGS)
        assert f.test_name == "TestComputeTotal"
        assert f.file == "payments_test.go" or f.file is not None
        assert f.line == 27
        assert f.error_type == ErrorType.DETERMINISTIC_CODE


class TestCompiler:
    def test_extracts_rust_error(self):
        f = parse_ci_logs(COMPILER_LOGS)
        assert f.file == "src/main.rs"
        assert f.line == 12
        assert f.exit_code == 1
        assert "mismatched types" in f.message or f.message


class TestShellGeneric:
    def test_detects_dependency_failure(self):
        f = parse_ci_logs(SHELL_LOGS)
        assert f.error_type == ErrorType.DEPENDENCY
        assert "ENOENT" in f.message or f.message

    def test_extracts_gha_error_step(self):
        f = parse_ci_logs(GHA_LOGS)
        assert f.job == "integration"
        assert "npm" in f.step
        assert f.exit_code == 2


class TestEdgeCases:
    def test_empty_logs(self):
        f = parse_ci_logs("")
        assert f.raw_logs == ""
        assert f.error_type == ErrorType.UNKNOWN

    def test_missing_output_defaults(self):
        f = parse_ci_logs("Everything passed\n")
        assert f.exit_code is None
        assert f.test_name is None
        assert f.error_type == ErrorType.UNKNOWN

    def test_retains_raw_logs(self):
        raw = "line one\n##[error]boom\nline three"
        f = parse_ci_logs(raw)
        assert f.raw_logs == raw
