"""FastAPI dependency injection.

Provides generators that FastAPI's ``Depends()`` system can use to inject
DB sessions, the triage repository, and the global metrics object into
route handlers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.engine import get_session
from app.db.repository import TriageRepository
from app.observability.metrics import Metrics, metrics


def get_repository(session: Annotated[Session, Depends(get_session)]) -> TriageRepository:
    """Yield a ``TriageRepository`` bound to the request-scoped session."""
    return TriageRepository(session)


def get_metrics() -> Metrics:
    """Return the global metrics singleton."""
    return metrics
