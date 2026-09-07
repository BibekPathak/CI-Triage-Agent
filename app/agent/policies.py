"""Command policy engine.

The agent NEVER gets unrestricted host shell access. This module provides:

* :class:`CommandPolicy` - a declarative, configurable allow/deny policy for
  shell commands run inside a sandbox.
* :func:`validate_command` - the enforcement entry point.

Security is enforced programmatically here, NOT via prompt instructions.
Common agent workflows (tests, linters, type checkers, git writes, language
runtimes) are allowed; destructive / host-escaping commands are rejected.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from app.models.domain import ActionClass

# --------------------------------------------------------------------------- #
# Policy definitions
# --------------------------------------------------------------------------- #
ALLOWED_COMMANDS: set[str] = {
    # test runners
    "pytest",
    "npm",
    "cargo",
    "go",
    "mvn",
    "gradle",
    "make",
    # linters / type checkers
    "ruff",
    "mypy",
    "eslint",
    "flake8",
    "black",
    "isort",
    "pyright",
    # language runtimes
    "python",
    "python3",
    "node",
    "go.",
    "ts-node",
    # git
    "git",
    # misc build/sys utilities allowed in a read-only-ish capacity
    "ls",
    "cat",
    "echo",
    "pwd",
    "cp",
    "mv",
    "mkdir",
    "touch",
    "which",
    "env",
    "uname",
    "head",
    "tail",
    "grep",
    "wc",
    "sed",
    "awk",
}

# Reasonably safe flags, subcommands, etc. that need no special handling.
# NOTE: ``&&`` / ``;`` / ``|`` command chaining and shell metacharacters are
# rejected to keep execution deterministic and avoid shell injection.
DENIED_COMMANDS: set[str] = {
    "sudo",
    "su",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "mkfs",
    "fdisk",
    "mount",
    "umount",
    "dd",
    "docker",
    "podman",
    "ssh",
    "scp",
    "telnet",
    "nc",
    "ncat",
    "nc.traditional",
    "rm",
    "chmod",
    "chown",
    "curl",
    "wget",
    "perl",
    "bash",
    "sh",
    "zsh",
    "fish",
    "kill",
    "killall",
    "pkill",
    "systemctl",
    "service",
    "crontab",
    "at",
    "iptables",
}

# Denied flag/argument fragments that indicate host or destructive intent.
DENIED_SUBSTRINGS: tuple[str, ...] = (
    "--no-sandbox",
    "--privileged",
    "2>/dev/null;",
    "/etc/",
    "/root/",
    "~",
    "eval ",
    "$(",
    "`",
    "proxy",
)


class CommandDeniedError(Exception):
    """Raised when a shell command violates the command policy."""


@dataclass(frozen=True)
class CommandPolicy:
    """Configurable allow/deny policy for sandboxed commands."""

    allowed: set[str] = field(default_factory=lambda: set(ALLOWED_COMMANDS))
    denied: set[str] = field(default_factory=lambda: set(DENIED_COMMANDS))
    denied_substrings: tuple[str, ...] = DENIED_SUBSTRINGS
    action_class: ActionClass = ActionClass.SANDBOX_WRITE

    def validate_command(
        self,
        command: str,
        action_class: ActionClass | None = None,
    ) -> str:
        """Validate ``command`` against policy.

        Returns the parsed argv list as a normalized string. Raises
        :class:`CommandDeniedError` if the command is not permitted.

        Args:
            command: The raw command line.
            action_class: Overrides the declared action class of the tool.
                READ_ONLY tools are always permitted to run any allowlisted
                command; REMOTE_WRITE / DESTRUCTIVE require approved policy.
        """
        if not command or not command.strip():
            raise CommandDeniedError("Empty command")

        # Reject shell control operators & subshells to prevent injection.
        for tok in ("&&", ";", "|", "$(", "`"):
            if tok in command:
                raise CommandDeniedError(f"Shell control operator not allowed: {tok!r}")

        try:
            argv = shlex.split(command)
        except ValueError as exc:  # unbalanced quotes
            raise CommandDeniedError(f"Could not parse command: {exc}") from exc

        if not argv:
            raise CommandDeniedError("Empty command")

        base = argv[0]
        base_normalized = base.replace("./", "").split("/")[-1]

        # Danger flag fragments anywhere in the command.
        for frag in self.denied_substrings:
            if frag in command:
                raise CommandDeniedError(f"Denied substring in command: {frag!r}")

        if base_normalized in self.denied and base_normalized not in self.allowed:
            raise CommandDeniedError(f"Command denied by policy: {base!r}")

        # npm is allowed only for test/run/exec scripts, never install of unknown
        # packages or git exec.
        if base_normalized == "npm":
            sub = argv[1] if len(argv) > 1 else ""
            if sub in {"install", "i", "add", "init", "cache", "config"}:
                raise CommandDeniedError("npm install/add/config denied by policy")

        # Go needs the explicit run/test/build subcommand.
        if base_normalized == "go":
            sub = argv[1] if len(argv) > 1 else ""
            if sub not in {"test", "build", "run", "vet", "fmt"}:
                raise CommandDeniedError(f"go subcommand not allowed: {sub!r}")

        # cargo only for read/test/build.
        if base_normalized == "cargo":
            sub = argv[1] if len(argv) > 1 else ""
            if sub not in {"test", "build", "check", "clippy", "fmt"}:
                raise CommandDeniedError(f"cargo subcommand not allowed: {sub!r}")

        if base_normalized not in self.allowed and base_normalized not in self.denied:
            raise CommandDeniedError(f"Command not in allowlist: {base!r}")

        return " ".join(argv)


# A module-level default policy instance for convenience.
DEFAULT_POLICY = CommandPolicy()


# --------------------------------------------------------------------------- #
# Action policy engine
# --------------------------------------------------------------------------- #
class ActionDeniedError(Exception):
    """Raised when a tool action is not permitted by the policy engine."""


class PolicyEngine:
    """Decides whether a tool action is allowed given its action class.

    The decision is purely mechanical and based on the declared
    :class:`ActionClass` of the tool plus the current approval status. The LLM
    never decides policy -- it only proposes tool calls; this engine either
    permits or blocks them.
    """

    def __init__(self, approve_mode: str = "manual") -> None:
        self.approve_mode = approve_mode

    def check(
        self,
        action_class: ActionClass,
        approval_granted: bool = False,
        *,
        requires_approval: bool = False,
    ) -> bool:
        """Return True if the action of ``action_class`` is permitted.

        Rules:
        * READ_ONLY       -> always allowed.
        * SANDBOX_WRITE   -> always allowed (isolated workspace only).
        * REMOTE_WRITE    -> allowed only after explicit approval.
        * DESTRUCTIVE     -> allowed only with approval and only in ``auto``
          mode (destructive actions still require human approval otherwise).
        """
        if action_class == ActionClass.READ_ONLY:
            return True
        if action_class == ActionClass.SANDBOX_WRITE:
            return True
        if action_class in (ActionClass.REMOTE_WRITE, ActionClass.DESTRUCTIVE):
            if approval_granted:
                return True
            return self.approve_mode == "auto"
        return False

    def require(
        self,
        action_class: ActionClass,
        approval_granted: bool = False,
        *,
        requires_approval: bool = False,
    ) -> None:
        """Like :meth:`check` but raises :class:`ActionDeniedError` if denied."""
        if not self.check(
            action_class,
            approval_granted,
            requires_approval=requires_approval,
        ):
            raise ActionDeniedError(
                f"Action '{action_class.value}' requires approval and is not granted"
            )

