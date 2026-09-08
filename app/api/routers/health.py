"""Health check router."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas import HealthResponse
from app.db.engine import get_session

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(session: Session = Depends(get_session)) -> HealthResponse:  # noqa: B008
    """Liveness / readiness probe."""
    db_status = "ok"
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "error"
    return HealthResponse(database=db_status)
