"""API routers package."""

from app.api.routers.health import router as health_router
from app.api.routers.triage import router as triage_router

__all__ = ["health_router", "triage_router"]
