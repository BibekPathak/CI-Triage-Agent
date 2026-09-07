"""CI log parsing and failure classification.

These pure functions convert raw CI log text into a structured
:class:`CIFailure`. They are deterministic and unit-tested; the LLM is NOT used
for structural extraction because that is better handled by rules here (fast,
cheap, testable). The LLM is reserved for higher-level diagnosis.
"""

from __future__ import annotations

import re

from app.models.domain import CIFailure, ErrorType

# Typical pytest short test summary:  "FAILED tests/test_payment.py::test_refund"
_FAILED_TEST = re.compile(r"FAILED\s+([\w./\\\-]+\.py)::([\w\[\]/]+)")

# Line numbers from tooling / assertions, e.g. "  file.py:42, in foo"
_LOCATION = re.compile(r"([\w./\\\-]+\.py):(\d+)")

_STACKTRACE_START = re.compile(r"^Traceback \(most recent call last\)", re.MULTILINE)

_ERROR_SIGNS: list[tuple[ErrorType, tuple[re.Pattern, ...]]] = [
    (ErrorType.TIMEOUT, (re.compile(r"timed?\s*out|TimeoutExpired|exceeded.*timeout", re.I),)),
    (
        ErrorType.DEPENDENCY,
        (
            re.compile(r"No module named|ModuleNotFoundError|ImportError", re.I),
            re.compile(
                r"fail(?:ed)? to (?:install|resolve|download)|Could not resolve|npm ERR", re.I
            ),
        ),
    ),
    (
        ErrorType.PERMISSION,
        (
            re.compile(r"Permission denied|EACCES|not authorized|401|403", re.I),
        ),
    ),
    (
        ErrorType.INFRASTRUCTURE,
        (
            re.compile(
                r"connection refused|ECONNREFUSED|service unavailable|502|504|runner has failed",
                re.I,
            ),
            re.compile(
                r"container.*(?:error|killed|unhealthy)|Killed|OOMKilled|no space left", re.I
            ),
        ),
    ),
    (
        ErrorType.ENVIRONMENT_CONFIG,
        (
            re.compile(r"command not found|No such file|not found: [\w]+|undefined variable", re.I),
        ),
    ),
]

# Common exception names: a strong deterministic-code signal.
_EXCEPTION_PATTERN = re.compile(
    r"\b(AssertionError|ValueError|TypeError|KeyError|IndexError|AttributeError|"
    r"RuntimeError|NameError|ZeroDivisionError|StopIteration|OverflowError|"
    r"RecursionError|EOFError|NotImplementedError|KeyboardInterrupt)\b"
)


def parse_ci_logs(raw_logs: str) -> CIFailure:
    """Extract a structured :class:`CIFailure` from raw CI log text.

    Best-effort: missing fields remain empty/None. The classifier then assigns
    the error category.
    """
    logs = raw_logs or ""
    failure = CIFailure(raw_logs=logs)

    m = _FAILED_TEST.search(logs)
    if m:
        failure.file = m.group(1)
        failure.test_name = m.group(2)

    if failure.file is None:
        loc = _LOCATION.search(logs)
        if loc:
            failure.file = loc.group(1)
            failure.line = int(loc.group(2))

    # Extract a message: the line containing the exception/assertion or ERROR.
    msg = _extract_message(logs)
    failure.message = msg

    st = _STACKTRACE_START.search(logs)
    if st:
        failure.stack_trace = logs[st.start() : st.start() + 2000]

    failure.error_type = classify(logs, failure)
    return failure


def _extract_message(logs: str) -> str:
    """Best-effort extraction of a one-line error message."""
    for line in logs.splitlines():
        strip = line.strip()
        if not strip:
            continue
        if _EXCEPTION_PATTERN.search(strip) or "Error:" in strip or "error:" in strip:
            return strip[:400]
        if strip.startswith("E ") or strip.startswith("FAILED"):
            return strip[:400]
    return ""


def classify(logs: str, failure: CIFailure | None = None) -> ErrorType:
    """Classify the failure category from log content.

    Order matters: infrastructure/tooling signals are only chosen when there is
    no strong deterministic-code signal, so we never misattribute a real code
    bug to infrastructure.
    """
    if not logs:
        return ErrorType.UNKNOWN

    # A present exception or assertion strongly implies deterministic code.
    if _EXCEPTION_PATTERN.search(logs) or re.search(r"AssertionError|assert ", logs):
        return ErrorType.DETERMINISTIC_CODE

    for etype, patterns in _ERROR_SIGNS:
        for pat in patterns:
            if pat.search(logs):
                return etype
    return ErrorType.UNKNOWN


def is_infrastructure(failure: CIFailure) -> bool:
    """True if the failure is infrastructure-related (do NOT auto-patch code)."""
    return failure.error_type in {
        ErrorType.INFRASTRUCTURE,
        ErrorType.DEPENDENCY,
        ErrorType.ENVIRONMENT_CONFIG,
        ErrorType.TIMEOUT,
        ErrorType.PERMISSION,
    }
