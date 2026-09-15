"""Robust CI log parser (multi-toolchain).

Extracts structured failure information from raw CI log text across common
toolchains: pytest, Jest/npm, cargo test, Go test, compiler errors, and
generic shell failures.

This module is intentionally regex-based and deterministic (no LLM). It
produces a best-effort :class:`CIFailure` while always retaining the raw logs.
The existing ``app/agent/parser.py`` handles *classification* (ErrorType +
``is_infrastructure``) and is reused here.

Supported extraction targets:
- workflow / job / step (GitHub Actions `##[group]` / `Run ` markers)
- exit code
- file / line
- test name
- error message
- stack trace
"""

from __future__ import annotations

import re

from app.agent.parser import classify, is_infrastructure
from app.models.domain import CIFailure, ErrorType

# --------------------------------------------------------------------------- #
# GitHub Actions log structure markers
# --------------------------------------------------------------------------- #
# A job header in GHA logs:  "1. Run npm test"  or  "##[group]Job: test"
_JOB = re.compile(r"^##\[group\](?:Job|Run|job):\s*(.+)$", re.MULTILINE)
# A step grouping:  "##[group]Run <command>"  blocks a set of log lines.
_STEP = re.compile(r"^##\[group\]Run\s+(.+)$", re.MULTILINE)
_STEP_INLINE = re.compile(r"^##\[group\](.+)$", re.MULTILINE)

# A line like  "ERROR [3/5] ... " or  "##[error]msg"  indicates the failing
# step in many runners.
_GHA_ERROR = re.compile(r"^##\[error\](.*)$", re.MULTILINE)

# Common exit-code markers, e.g. "The process '/usr/bin/python' failed with
# exit code 1", "Exit code: 1", "Process completed with exit code 1".
_EXIT_CODE = re.compile(
    r"exit code[:=\s]+(\d+)|failed with exit code (\d+)|completed with exit code (\d+)",
    re.I,
)

# A line such as  "> Run the failing command"  printed after the failure.
_LAST_COMMAND = re.compile(r"^>\s*(.+)$", re.MULTILINE)

# --------------------------------------------------------------------------- #
# Toolchain-specific test failure markers
# --------------------------------------------------------------------------- #
# pytest:       "FAILED tests/test_api.py::test_refund"
_PYTEST_FAILED = re.compile(r"FAILED\s+([\w./\\\-]+\.py)::([\w\[\]/]+)")

# pytest location:  "  file.py:42: in test_x"
_LOCATION = re.compile(r"([\w./\\\-]+\.py):(\d+):?")

# Jest/npm:  "  at Object.<anonymous> (test/api.test.js:25:13)"  and
#             "FAIL test/api.test.js"
_JEST_FAIL = re.compile(r"^FAIL\s+([\w./\\\-]+\.(?:js|jsx|ts|tsx))", re.MULTILINE)
_JEST_AT = re.compile(r"\(([\w./\\\-]+\.(?:js|jsx|ts|tsx)):(\d+):(\d+)\)")

# cargo test:  "---- tests::foo stdout ----"  or  "test tests::foo ... FAILED"
_CARGO_TEST = re.compile(r"----\s+([\w:]+)\s+stdout\s+----")
_CARGO_FAIL = re.compile(r"^test\s+([\w:]+)\s+\.\.\.\s+FAILED", re.MULTILINE)

# Go test:  "--- FAIL: TestFoo (0.00s)"  or  "    foo_test.go:42: expected"
_GO_FAIL = re.compile(r"^---\s+FAIL:\s*([\w/]+)\s", re.MULTILINE)
_GO_LOCATION = re.compile(r"^(\s+)([\w./\-]+_test\.go):(\d+):", re.MULTILINE)

# Compiler errors (Rust, C/C++, TypeScript):
#   "error[E0308]: mismatched types"           (rustc)
#   "src/main.rs:12:9: error: ..."             (rustc)
#   "main.c:5:1: error: expected ..."          (gcc/clang)
#   "error TS1234: ..."                        (tsc)
_COMPILER_ERROR = re.compile(
    r"\berror(?:\s*\[[E\d]+\])?[:\s]+\S+", re.MULTILINE
)
_COMPILER_LOCATION = re.compile(
    r"([\w./\\\-]+\.(?:rs|c|cc|cpp|h|hpp|ts|js)):(\d+):(\d+)"
)

# Generic assertion/exception for detecting a stack trace.
_EXCEPTION_PATTERN = re.compile(
    r"\b(AssertionError|ValueError|TypeError|KeyError|IndexError|AttributeError|"
    r"RuntimeError|NameError|ZeroDivisionError|OverflowError|RecursionError|"
    r"EOFError|NotImplementedError)\b"
)
_STACKTRACE_START = re.compile(
    r"^Traceback \(most recent call last\)|^\s*Stack trace:", re.MULTILINE
)


def _first(regex: re.Pattern, text: str) -> str | None:
    m = regex.search(text or "")
    return m.group(1).strip() if m else None


def _is_toolchain_test_failure(logs: str) -> bool:
    """Detect deterministic-code test/compiler failures that the generic
    classifier (Python-centric) may miss (e.g. Jest, go test, cargo, rustc)."""
    text = logs or ""
    return bool(
        _JEST_FAIL.search(text)
        or re.search(r"Expected:\s|Received:\s", text)  # Jest
        or _GO_FAIL.search(text)  # "--- FAIL: TestX"
        or _CARGO_FAIL.search(text)
        or _CARGO_TEST.search(text)  # cargo "FAILED"
        or re.search(r"\berror\[[E\d]+\]", text)  # rustc "error[E0308]"
    )


def _extract_exit_code(logs: str) -> int | None:
    m = _EXIT_CODE.search(logs or "")
    if not m:
        return None
    for grp in m.groups():
        if grp:
            return int(grp)
    return None


def _find_file_line(logs: str) -> tuple[str | None, int | None]:
    """Find a (file, line) pair from any of the supported toolchains."""
    # Prefer an explicit pytest location.
    m = _LOCATION.search(logs or "")
    if m:
        return m.group(1), int(m.group(2))
    m = _JEST_AT.search(logs or "")
    if m:
        return m.group(1), int(m.group(2))
    m = _GO_LOCATION.search(logs or "")
    if m:
        return m.group(2), int(m.group(3))
    m = _COMPILER_LOCATION.search(logs or "")
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def _find_test_name(logs: str) -> str | None:
    # pytest:  "FAILED tests/test_payment.py::test_refund" -> test name last.
    m = _PYTEST_FAILED.search(logs or "")
    if m:
        return m.group(2)
    # Other toolchains put the test name in group(1).
    for regex in (_JEST_FAIL, _CARGO_TEST, _CARGO_FAIL, _GO_FAIL):
        m2 = regex.search(logs or "")
        if m2:
            return m2.group(1)
    return None


def _extract_message(logs: str) -> str:
    """Best-effort one-line error message."""
    for line in (logs or "").splitlines():
        strip = line.strip()
        if not strip:
            continue
        if _EXCEPTION_PATTERN.search(strip) or "Error:" in strip or "error:" in strip:
            return strip[:400]
        if strip.startswith("E ") or strip.startswith("FAILED"):
            return strip[:400]
        if strip.startswith("error["):  # rustc "error[E0308]: ..."
            return strip[:400]
        if strip.startswith("npm ERR!"):  # npm error block
            return strip[:400]
    return ""


def _extract_stack_trace(logs: str) -> str | None:
    m = _STACKTRACE_START.search(logs or "")
    if m:
        return (logs or "")[m.start() : m.start() + 2000]
    return None


def _extract_step(logs: str) -> str:
    # Prefer a `Run <command>` step header, then the ##[error] line.
    run = _first(_STEP, logs or "")
    if run:
        return run
    err = _first(_GHA_ERROR, logs or "")
    if err:
        return err
    return ""


def _extract_job(logs: str) -> str:
    return _first(_JOB, logs or "") or ""


def parse_ci_logs(raw_logs: str) -> CIFailure:
    """Parse raw CI logs into a structured :class:`CIFailure`.

    Best-effort for every field; raw logs are always retained. Errors that the
    classifier deems infrastructure-related are marked as such (and the caller
    should not attempt to patch them).
    """
    logs = raw_logs or ""
    failure = CIFailure(raw_logs=logs)

    # Ancillary CI metadata (workflow name/client-provided, job, step).
    failure.job = _extract_job(logs)
    failure.step = _extract_step(logs)

    # Test / file / line.
    failure.test_name = _find_test_name(logs)
    file_, line = _find_file_line(logs)
    failure.file = file_ or failure.file
    failure.line = line

    # Exit code, message, stack trace.
    failure.exit_code = _extract_exit_code(logs)
    failure.message = _extract_message(logs)
    failure.stack_trace = _extract_stack_trace(logs)

    # Classify (reuses the existing deterministic classifier), falling back to
    # toolchain-specific signals for non-Python test/compiler failures.
    failure.error_type = classify(logs, failure)
    if failure.error_type == ErrorType.UNKNOWN and _is_toolchain_test_failure(logs):
        failure.error_type = ErrorType.DETERMINISTIC_CODE
    return failure


__all__ = ["CIFailure", "is_infrastructure", "parse_ci_logs"]
