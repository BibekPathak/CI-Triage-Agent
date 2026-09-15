"""Workspace preparation for a triage run.

Creates a dedicated filesystem workspace (outside the repo) and checks out the
failing revision into it.  The returned path is used as the sandbox
``workspace_root`` so the agent debugs the exact CI revision in isolation.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.repo.checkout import (
    CheckoutResult,
    RepoCheckoutError,
    checkout_repository,
)


@dataclass
class Workspace:
    """A prepared debugging workspace."""

    root: str
    checkout: CheckoutResult | None = None
    owns_root: bool = True

    def cleanup(self) -> None:
        """Remove the workspace root if this workspace created it."""
        if self.owns_root:
            import shutil

            shutil.rmtree(self.root, ignore_errors=True)


def prepare_workspace(
    repository: str,
    sha: str | None = None,
    *,
    root: str = "",
    token: str | None = None,
) -> Workspace:
    """Prepare a workspace for *repository* at *sha*.

    If *root* is empty, a fresh temporary directory is created (and owned, so
    ``Workspace.cleanup()`` removes it).  Returns a :class:`Workspace` whose
    ``root`` can be passed to the sandbox manager.
    """
    owns_root = not root
    root_path = Path(root) if root else Path(tempfile.mkdtemp(prefix="cta-workspace-"))
    root_path.mkdir(parents=True, exist_ok=True)

    result = None
    try:
        result = checkout_repository(
            repository=repository,
            sha=sha,
            destination=str(root_path),
            token=token,
        )
    except RepoCheckoutError:
        if owns_root:
            import shutil

            shutil.rmtree(root_path, ignore_errors=True)
        raise

    return Workspace(root=str(root_path), checkout=result, owns_root=owns_root)


__all__ = ["Workspace", "prepare_workspace"]
