"""SQLAlchemy engine and session factory.

Provides a synchronous engine (works with both SQLite and PostgreSQL) and
a session factory for dependency injection.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def create_engine(url: str | None = None) -> None:
    """Initialise the global engine and session factory."""
    global _engine, _SessionLocal  # noqa: PLW0603

    db_url = url or settings.database_url
    _engine = sa_create_engine(
        db_url,
        echo=False,
        future=True,
        # SQLite needs check_same_thread=False for multi-threaded access.
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    # Create tables if they don't exist (safe for SQLite dev usage).
    Base.metadata.create_all(bind=_engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI-compatible dependency that yields a session, committing on
    success and rolling back on error before closing."""
    if _SessionLocal is None:
        create_engine()
    session = _SessionLocal()  # type: ignore[misc]
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager that commits on success, rolls back on error."""
    if _SessionLocal is None:
        create_engine()
    session = _SessionLocal()  # type: ignore[misc]
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_factory() -> Session:
    """Create a new session (for use as a FastAPI dependency)."""
    if _SessionLocal is None:
        create_engine()
    return _SessionLocal()  # type: ignore[misc]


# Convenience alias for dependency injection.
SessionLocal = session_factory
