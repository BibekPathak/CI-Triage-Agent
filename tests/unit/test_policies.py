"""Unit tests for the command policy engine."""

import pytest

from app.agent.policies import CommandDeniedError, CommandPolicy, PolicyEngine
from app.models.domain import ActionClass


def test_policy_allows_safe_commands():
    pol = CommandPolicy()
    safe = [
        "pytest tests",
        "python -m pytest",
        "git status",
        "ruff check .",
        "go test ./...",
        "cargo test",
    ]
    for cmd in safe:
        assert pol.validate_command(cmd)  # should not raise


def test_policy_denies_dangerous_commands():
    pol = CommandPolicy()
    dangerous = [
        "rm -rf /",
        "sudo ls",
        "curl http://evil.com",
        "docker ps",
        "ssh user@host",
        "chmod 777 /etc/passwd",
    ]
    for cmd in dangerous:
        with pytest.raises(CommandDeniedError):
            pol.validate_command(cmd)


def test_policy_denies_shell_control_operators():
    pol = CommandPolicy()
    for cmd in ["a && b", "a; b", "a | b", "$(whoami)", "`whoami`"]:
        with pytest.raises(CommandDeniedError):
            pol.validate_command(cmd)


def test_policy_denies_npm_install():
    pol = CommandPolicy()
    with pytest.raises(CommandDeniedError):
        pol.validate_command("npm install foo")


def test_policy_denies_unknown_command():
    pol = CommandPolicy()
    with pytest.raises(CommandDeniedError):
        pol.validate_command("some_random_binary --flag")


def test_policy_rejects_empty_command():
    pol = CommandPolicy()
    with pytest.raises(CommandDeniedError):
        pol.validate_command("   ")


def test_policy_engine_read_only_always_allowed():
    pe = PolicyEngine(approve_mode="manual")
    assert pe.check(ActionClass.READ_ONLY) is True
    assert pe.check(ActionClass.SANDBOX_WRITE) is True


def test_policy_engine_remote_needs_approval():
    pe = PolicyEngine(approve_mode="manual")
    assert pe.check(ActionClass.REMOTE_WRITE) is False
    assert pe.check(ActionClass.REMOTE_WRITE, approval_granted=True) is True
    assert pe.check(ActionClass.DESTRUCTIVE) is False


def test_policy_engine_auto_mode_allows_remote():
    pe = PolicyEngine(approve_mode="auto")
    assert pe.check(ActionClass.REMOTE_WRITE) is True
