"""Tests for the API layer (FastAPI routers + app factory)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.deps import get_metrics, get_repository
from app.db.engine import Base, get_session
from app.db.repository import TriageRepository
from app.models.domain import RunStatus
from app.models.state import TriageState
from app.observability.metrics import Metrics

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def db_session():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient with overridden DB + metrics dependencies."""
    app = create_app()

    def _override_session():
        yield db_session

    def _override_repo():
        return TriageRepository(db_session)

    test_metrics = Metrics()

    def _override_metrics():
        return test_metrics

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_repository] = _override_repo
    app.dependency_overrides[get_metrics] = _override_metrics

    with TestClient(app) as c:
        yield c, test_metrics


def _seed_state(session, triage_id: str = "test123") -> None:
    state = TriageState(
        triage_id=triage_id,
        repository="owner/repo",
        workflow_run_id="42",
        status=RunStatus.COMPLETED,
        root_cause="SyntaxError",
    )
    from app.db.repository import state_to_row
    session.add(state_to_row(state))
    session.commit()


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_ok(self, client):
        c, _ = client
        resp = c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_metrics_snapshot(self, client):
        c, m = client
        m.counter("test_counter").inc(5)
        resp = c.get("/api/v1/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data
        assert data["counters"]["test_counter"] == 5.0


# ------------------------------------------------------------------
# Triage CRUD
# ------------------------------------------------------------------

class TestCreateTriage:
    def test_create(self, client):
        c, m = client
        resp = c.post("/api/v1/triage", json={
            "repository": "owner/repo",
            "workflow_run_id": "99",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["repository"] == "owner/repo"
        assert data["workflow_run_id"] == "99"
        assert data["status"] == "initializing"


class TestListTriages:
    def test_list_empty(self, client):
        c, _ = client
        resp = c.get("/api/v1/triage")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_data(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "r1")
        _seed_state(db_session, "r2")
        resp = c.get("/api/v1/triage")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_filter_by_repo(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "r1")
        resp = c.get("/api/v1/triage?repository=owner/repo")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_filter_by_status(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "r1")
        resp = c.get("/api/v1/triage?status=completed")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestGetTriage:
    def test_get_ok(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "abc123")
        resp = c.get("/api/v1/triage/abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["triage_id"] == "abc123"
        assert data["repository"] == "owner/repo"

    def test_get_not_found(self, client):
        c, _ = client
        resp = c.get("/api/v1/triage/nope")
        assert resp.status_code == 404


class TestApproveTriage:
    def test_approve(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "abc123")
        resp = c.post("/api/v1/triage/abc123/approve", json={
            "approve": True,
            "comment": "looks good",
        })
        assert resp.status_code == 200
        assert resp.json()["approval_status"] == "approved"

    def test_reject(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "abc123")
        resp = c.post("/api/v1/triage/abc123/approve", json={
            "approve": False,
        })
        assert resp.status_code == 200
        assert resp.json()["approval_status"] == "rejected"

    def test_approve_not_found(self, client):
        c, _ = client
        resp = c.post("/api/v1/triage/nope/approve", json={"approve": True})
        assert resp.status_code == 404


class TestDeleteTriage:
    def test_delete(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "abc123")
        resp = c.delete("/api/v1/triage/abc123")
        assert resp.status_code == 204

    def test_delete_not_found(self, client):
        c, _ = client
        resp = c.delete("/api/v1/triage/nope")
        assert resp.status_code == 404


class TestTriageEvents:
    def test_events_empty(self, client, db_session):
        c, _ = client
        _seed_state(db_session, "abc123")
        resp = c.get("/api/v1/triage/abc123/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_events_not_found(self, client):
        c, _ = client
        resp = c.get("/api/v1/triage/nope/events")
        assert resp.status_code == 404
