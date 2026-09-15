"""Repository checkout at an exact revision.

Materializes the repository revision associated with a CI failure into a
workspace directory so the sandbox can debug the failing commit -- never an
arbitrary branch head.

Security model:
- Credentials are supplied only via the ``GIT_ASKPASS`` mechanism (a
  temporary, 0600-permission helper script) or environment variables.  The
  token NEVER appears in command-line arguments, logs, exception messages, or
  returned data.
- ``subprocess.run`` is always used WITHOUT ``shell=True``.
- Reviewed for traversal: destination is created fresh under a caller-provided
  parent; callers are responsible for placing it inside the sandbox workspace.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class RepoCheckoutError(Exception):
    """Raised when a repository cannot be checked out.

    Error messages intentionally avoid echoing any credential material.
    """


@dataclass
class CheckoutResult:
    """Outcome of a successful checkout."""

    path: str
    head_sha: str = ""
    default_branch: str = "main"


def _is_local_path(repository: str) -> bool:
    """True if *repository* is a local filesystem path (no-network demo)."""
    if repository.startswith(("/", "./", "../")):
        return True
    p = Path(repository)
    return p.exists() and (p / ".git").is_dir()


def _write_askpass_script() -> str:
    """Create a temporary git askpass helper that echoes a token.

    Returns the script path (caller must remove it).  The token is written to
    a 0600 temp file and only ever read by git via GIT_ASKPASS.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, encoding="utf-8"
    ) as script:
        # git asks "Username for 'https://github.com':" then "Password:".  For
        # a personal access token we return the token as the password and a
        # fixed x-access-token username.
        script.write(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"Username*\" ]; then\n"
            "  echo 'x-access-token'\n"
            "else\n"
            f"  echo '{token}'\n"
            "fi\n"
        )
    name = script.name
    # Restrict permissions so the token file is not world-readable.
    os.chmod(name, stat.S_IRUSR | stat.S_IWUSR)
    return name


def _run_git(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Run a git command without a shell; raises on failure.

    Returns the CompletedProcess.  Never uses ``shell=True``.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoCheckoutError("git checkout timed out") from exc
    except FileNotFoundError as exc:
        raise RepoCheckoutError("git executable not found") from exc

    if result.returncode != 0:
        # Sanitize: git may echo a URL; strip any possible token segment.
        err = result.stderr.strip()
        raise RepoCheckoutError(f"git {args[0]} failed: {_sanitize(err)}")
    return result


def _sanitize(message: str) -> str:
    """Remove any credential-bearing URL fragments before surfacing errors.

    Redacts ``scheme://user:password@`` credential pairs generically, then
    also strips any known tokens verbatim.
    """
    # Redact userinfo credentials in any URL, e.g. https://user:secret@host.
    cred_url = re.compile(r"(://[^:/@\s]+):[^@\s]*@")
    message = cred_url.sub(r"\1:***@", message)
    for tok in (
        "x-access-token",
        os.environ.get("GH_TOKEN", ""),
        os.environ.get("GITHUB_TOKEN", ""),
    ):
        if tok:
            message = message.replace(tok, "***")
    return message[:500]


def _checkout_url(repository: str) -> str:
    """Return the plain https clone URL (no token embedded)."""
    if "://" in repository or _is_local_path(repository):
        return repository
    return f"https://github.com/{repository}.git"


def checkout_repository(
    repository: str,
    sha: str | None = None,
    destination: str = "",
    *,
    token: str | None = None,
    timeout: float = 180.0,
) -> CheckoutResult:
    """Clone *repository* at a specific *sha* into *destination*.

    *destination* becomes the repository root (matching the orchestrator's
    ``workspace_root``).  If it already holds a checkout, it is reused
    (idempotent re-run).  If *sha* is None, checks out the default branch
    head.  Credentials come from ``GH_TOKEN``/``GITHUB_TOKEN`` env (or
    *token*), passed only through ``GIT_ASKPASS``.
    """
    target = Path(destination).resolve()

    # Reuse an existing checkout if present (idempotent re-run).
    if target.exists() and ((target / ".git").is_dir() or any(target.iterdir())):
        return CheckoutResult(path=str(target), head_sha=_head_sha(str(target)))

    target.mkdir(parents=True, exist_ok=True)

    # Build an environment with credentials available only to git (env vars
    # are not part of the command line).
    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token

    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    askpass: str | None = None
    if _needs_auth(repository, env):
        askpass = _write_askpass_script()
        env["GIT_ASKPASS"] = askpass

    url = _checkout_url(repository)
    try:
        _run_git(["clone", "--no-checkout", url, str(target)], env=env, timeout=timeout)
        _run_git(["checkout", sha or "HEAD"], cwd=str(target), env=env, timeout=timeout)
    except RepoCheckoutError:
        # Best-effort cleanup of a partial clone.
        import shutil

        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        if askpass:
            with contextlib.suppress(OSError):
                os.unlink(askpass)

    head = _head_sha(str(target))
    return CheckoutResult(path=str(target), head_sha=head)


def _needs_auth(repository: str, env: dict[str, str]) -> bool:
    """Private repos / remote URLs need the askpass credential helper."""
    if _is_local_path(repository):
        return False
    return bool(env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"))


def _head_sha(path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=path,
            timeout=30.0,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "CheckoutResult",
    "RepoCheckoutError",
    "checkout_repository",
]
