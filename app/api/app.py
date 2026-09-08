"""FastAPI application factory.

Creates and configures the FastAPI ``app`` object used by Uvicorn.
Import as ``from app.api.app import app`` for ``uvicorn app.api.app:app``.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from app.api.deps import get_metrics
from app.api.routers import health_router, triage_router
from app.config import settings
from app.db.engine import create_engine
from app.observability.logging import setup_logging
from app.observability.metrics import Metrics


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    setup_logging(level=settings.log_level, fmt=settings.log_format)

    # Ensure DB engine + tables are initialised.
    create_engine()

    application = FastAPI(
        title="CI Triage Agent",
        version="0.1.0",
        description="Autonomous CI failure triage with human-in-the-loop approval.",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    application.include_router(health_router)
    application.include_router(triage_router)

    @application.get("/api/v1/metrics")
    def metrics_endpoint(m: Metrics = Depends(get_metrics)) -> dict:  # noqa: B008
        """Return the current metrics snapshot."""
        return m.snapshot()

    return application


# Module-level ``app`` for ``uvicorn app.api.app:app``.
app = create_app()
