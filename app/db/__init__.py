"""Database layer: SQLAlchemy models, engine, session, and repository.

Uses synchronous SQLAlchemy 2.0 with SQLite by default.  The full
``TriageState`` is persisted as a JSON blob; key fields are extracted for
filtering and querying.  Switch to PostgreSQL by setting ``DATABASE_URL``.
"""

from app.db.engine import SessionLocal, create_engine, get_session
from app.db.models import EventModel, TriageRunModel
from app.db.repository import TriageRepository

__all__ = [
    "EventModel",
    "SessionLocal",
    "TriageRepository",
    "TriageRunModel",
    "create_engine",
    "get_session",
]
