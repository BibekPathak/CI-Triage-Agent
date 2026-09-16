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
from app.config import settings
from app.db.repository import TriageRepository
from app.llm import build_provider
from app.models.state import TriageState
from app.observability.events import EventRecorder
from app.observability.logging import get_logger
from app.sandbox.manager import SandboxManager

logger = get_logger("service")


def build_orchestrator(
    recorder: EventRecorder | None = None,
    budget: Budget | None = None,
    workspace_root: str = "",
    sandbox_manager: SandboxManager | None = None,
) -> Orchestrator:
    """Construct an :class:`Orchestrator` from current settings.

    Uses the configured LLM provider (OpenAI by default; deterministic for
    offline / demo runs when ``LLM_PROVIDER=deterministic``).  When a
    ``workspace_root`` is provided, a sandbox (local backend by default,
    Docker in production) is created and every agent command runs through it.
    """
    llm = build_provider()
    planner = Planner(llm=llm)

    # Only wire a sandbox when we have a real workspace to confine execution.
    if workspace_root and sandbox_manager is None:
        sandbox_manager = SandboxManager(backend=settings.sandbox_backend)

    executor = Executor()
    return Orchestrator(
        planner=planner,
        executor=executor,
        recorder=recorder,
        budget=budget,
        workspace_root=workspace_root,
        sandbox_manager=sandbox_manager,
    )


def is_resumable(state: TriageState) -> bool:
    """True if a persisted run can be resumed at the approval boundary.

    Resumable means the run paused at ``AWAITING_APPROVAL`` with a captured,
    signed-off patch -- i.e. it can be picked up after a restart to open PR.
    """
    from app.models.domain import RunStatus

    return (
        state.status == RunStatus.AWAITING_APPROVAL
        and state.diff_signed_off
        and bool(state.candidate_patch)
    )


def resume_triage(
    triage_id: str,
    repo: TriageRepository,
) -> TriageState:
    """Load a persisted run and return it if it can be resumed.

    Raises :class:`RuntimeError` with a clear message otherwise, so callers can
    surface a 4xx/notice instead of silently approving an unusable run.
    """
    state = repo.get_state(triage_id)
    if state is None:
        raise RuntimeError(f"triage run not found: {triage_id}")
    if not is_resumable(state):
        raise RuntimeError(
            f"triage run {triage_id} is not resumable "
            f"(status={state.status.value}, patch={'yes' if state.candidate_patch else 'no'})"
        )
    return state


async def run_triage(
    repository: str,
    workflow_run_id: str,
    repo: TriageRepository | None = None,
    recorder: EventRecorder | None = None,
    budget: Budget | None = None,
    context_source: Callable[[TriageState], Awaitable[None]] | None = None,
    workspace_root: str = "",
    backend: str | None = None,
) -> None:
    """Run a full triage for a repo/run and persist the outcome.

    Intended for use in an async background task so the HTTP request returns
    immediately while the triage executes.  When ``repo`` is None, a fresh DB
    session/repository is created (for background use after the request
    session has closed).

    ``context_source`` populates the state's CI context before the agent runs.
    Defaults to a real GitHub-backed collector (see ``app.github.context``).

    When ``workspace_root`` is empty, a repository checkout is prepared in a
    temporary workspace and used as the sandbox workspace root.
    """
    from app.github.context import build_github_context_source
    from app.models.domain import RunStatus
    from app.repo import Workspace, prepare_workspace

    if context_source is None:
        context_source = build_github_context_source()

    owns_session = repo is None
    if repo is None:
        from app.db.engine import session_factory

        session = session_factory()
        repo = TriageRepository(session)

    # A prepared workspace (checkout) to confine agent execution.
    workspace: Workspace | None = None

    state: TriageState | None = None
    try:
        state = TriageState(repository=repository, workflow_run_id=workflow_run_id)
        recorder = recorder or EventRecorder()
        recorder.bind(state.triage_id)
        repo.save_state(state)
        repo.commit()

        # Materialize the failing revision into the workspace.  Best-effort:
        # a failed checkout (e.g. offline/private) logs a warning and the run
        # proceeds without a sandbox rather than aborting.
        if not workspace_root:
            try:
                workspace = prepare_workspace(repository, sha=None)
                workspace_root = workspace.root
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                logger.warning(
                    "workspace checkout failed; continuing sandbox-less: %s", exc
                )
                workspace_root = ""
        state.workspace_path = workspace_root
        repo.save_state(state)
        repo.commit()

        logger.info(
            "starting triage: triage_id=%s repo=%s run=%s workspace=%s",
            state.triage_id,
            repository,
            workflow_run_id,
            workspace_root,
            extra={"triage_id": state.triage_id, "repository": repository},
        )

        orchestrator = build_orchestrator(
            recorder=recorder,
            budget=budget,
            workspace_root=workspace_root,
            sandbox_manager=SandboxManager(backend=backend) if workspace_root else None,
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
        if workspace is not None:
            workspace.cleanup()
        if owns_session:
            session.close()
