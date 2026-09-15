"""Triage orchestration service.

Bridges the HTTP/CLI layers to the agent engine.  Builds the full dependency
graph (LLM -> planner -> executor -> orchestrator), runs a background triage,
and persists the resulting state + events to the database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agent.executor import Executor
from app.agent.orchestrator import Budget, Orchestrator
from app.agent.planner import Planner
from app.db.repository import TriageRepository
from app.llm import build_provider
from app.models.state import TriageState
from app.observability.events import EventRecorder
from app.observability.logging import get_logger

logger = get_logger("service")


def build_orchestrator(
    recorder: EventRecorder | None = None,
    budget: Budget | None = None,
) -> Orchestrator:
    """Construct an :class:`Orchestrator` from current settings.

    Uses the configured LLM provider (OpenAI by default; deterministic for
    offline / demo runs when ``LLM_PROVIDER=deterministic``).
    """
    llm = build_provider()
    planner = Planner(llm=llm)
    executor = Executor()
    return Orchestrator(
        planner=planner,
        executor=executor,
        recorder=recorder,
        budget=budget,
    )


async def run_triage(
    repository: str,
    workflow_run_id: str,
    repo: TriageRepository | None = None,
    recorder: EventRecorder | None = None,
    budget: Budget | None = None,
    context_source: Callable[[TriageState], Awaitable[None]] | None = None,
) -> None:
    """Run a full triage for a repo/run and persist the outcome.

    Intended for use in an async background task so the HTTP request returns
    immediately while the triage executes.  When ``repo`` is None, a fresh DB
    session/repository is created (for background use after the request
    session has closed).

    ``context_source`` populates the state's CI context before the agent runs.
    Defaults to a real GitHub-backed collector (see ``app.github.context``).
    """
    from app.github.context import build_github_context_source
    from app.models.domain import RunStatus

    if context_source is None:
        context_source = build_github_context_source()

    owns_session = repo is None
    if repo is None:
        from app.db.engine import session_factory

        session = session_factory()
        repo = TriageRepository(session)

    state: TriageState | None = None
    try:
        recorder = recorder or EventRecorder()
        orchestrator = build_orchestrator(recorder=recorder, budget=budget)

        state = TriageState(
            repository=repository,
            workflow_run_id=workflow_run_id,
            status=RunStatus.INITIALIZING,
        )
        # Bind the recorder to the run id and persist an initial row so the
        # run is immediately visible via the API.
        recorder.bind(state.triage_id)
        repo.save_state(state)
        repo.commit()

        logger.info(
            "starting triage: triage_id=%s repo=%s run=%s",
            state.triage_id,
            repository,
            workflow_run_id,
            extra={"triage_id": state.triage_id, "repository": repository},
        )

        outcome = await orchestrator.run(state, context_source)

        # Persist final state + drained events.
        repo.save_state(outcome.state)
        events = recorder.drain()
        if events:
            repo.save_events(events)
        repo.commit()

        logger.info(
            "triage finished: triage_id=%s status=%s reason=%s",
            state.triage_id,
            outcome.state.status.value,
            outcome.stop_reason,
            extra={"triage_id": state.triage_id},
        )
    except Exception as exc:  # noqa: BLE001 - persist failures for auditability
        if state is not None:
            logger.error(
                "triage failed: triage_id=%s error=%s",
                state.triage_id,
                exc,
                extra={"triage_id": state.triage_id},
            )
            try:
                state.status = RunStatus.FAILED
                state.last_error = str(exc)
                repo.save_state(state)
                repo.commit()
            except Exception:  # noqa: BLE001 - do not mask the original error
                logger.exception("failed to persist triage failure state")
        else:
            logger.error("triage failed before state was created: %s", exc)
    finally:
        if owns_session:
            session.close()
