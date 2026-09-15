"""Repository checkout and workspace preparation."""

from app.repo.checkout import (
    CheckoutResult,
    RepoCheckoutError,
    checkout_repository,
)
from app.repo.diff import (
    DiffResult,
    PatchOutcome,
    build_unified_diff,
    capture_diff,
    validate_unexpected_changes,
)
from app.repo.workspace import Workspace, prepare_workspace

__all__ = [
    "CheckoutResult",
    "DiffResult",
    "PatchOutcome",
    "RepoCheckoutError",
    "Workspace",
    "build_unified_diff",
    "capture_diff",
    "checkout_repository",
    "prepare_workspace",
    "validate_unexpected_changes",
]
