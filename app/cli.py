"""CLI entry point using Typer.

Usage::

    ci-triage serve          # start the API server
    ci-triage status <id>    # show triage run status
    ci-triage list           # list recent triage runs
    ci-triage demo           # run a demo triage with deterministic LLM
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="ci-triage",
    help="Autonomous CI Triage Agent",
    no_args_is_help=True,
)
console = Console()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
) -> None:
    """Start the API server (Uvicorn)."""
    import uvicorn

    uvicorn.run(
        "app.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def status(triage_id: str = typer.Argument(help="Triage run ID")) -> None:
    """Show the status of a triage run."""
    from app.db import TriageRepository
    from app.db.engine import create_engine, session_factory

    create_engine()
    session = session_factory()
    try:
        repo = TriageRepository(session)
        state = repo.get_state(triage_id)
        if state is None:
            console.print(f"[red]Triage run {triage_id} not found[/red]")
            raise typer.Exit(1)

        table = Table(title=f"Triage {state.triage_id}")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("Repository", state.repository)
        table.add_row("Workflow Run", state.workflow_run_id)
        table.add_row("Status", state.status.value)
        table.add_row("Root Cause", state.root_cause or "—")
        table.add_row("Confidence", str(state.confidence) if state.confidence else "—")
        table.add_row("PR Title", state.proposed_pr_title or "—")
        table.add_row("Approval", state.approval_status.value)
        table.add_row("Iterations", str(state.iterations))
        table.add_row("Tool Calls", str(state.tool_calls))
        table.add_row("Cost", f"${state.estimated_cost:.4f}")
        console.print(table)
    finally:
        session.close()


@app.command("list")
def list_runs(
    limit: int = typer.Option(20, help="Max runs to show"),
    repository: str | None = typer.Option(None, help="Filter by repo"),
) -> None:
    """List recent triage runs."""
    from app.db import TriageRepository
    from app.db.engine import create_engine, session_factory

    create_engine()
    session = session_factory()
    try:
        repo = TriageRepository(session)
        rows = repo.list_runs(repository=repository, limit=limit)

        if not rows:
            console.print("[dim]No triage runs found.[/dim]")
            return

        table = Table(title="Triage Runs")
        table.add_column("ID", style="cyan")
        table.add_column("Repository")
        table.add_column("Status")
        table.add_column("Root Cause")
        table.add_column("Created")

        for r in rows:
            cause = r.root_cause or "—"
            if len(cause) > 40:
                cause = cause[:40] + "…"
            table.add_row(
                r.triage_id[:12],
                r.repository,
                r.status,
                cause,
                r.created_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)
    finally:
        session.close()


@app.command()
def demo() -> None:
    """Run a demo triage with deterministic LLM (no API key needed)."""
    import asyncio

    from app.agent.executor import Executor
    from app.agent.orchestrator import Budget, Orchestrator
    from app.agent.planner import Planner
    from app.llm.deterministic import DeterministicLLM
    from app.models.state import TriageState

    console.print("[bold]Running demo triage…[/bold]")

    llm = DeterministicLLM()
    planner = Planner(llm=llm)
    executor = Executor(registry=None, policy=None)

    state = TriageState(
        repository="demo/repo",
        workflow_run_id="1",
    )

    async def _noop_ctx(s: TriageState) -> None:
        s.ci_logs = "FAILED test_foo\nAssertionError"

    orch = Orchestrator(planner=planner, executor=executor, budget=Budget(max_iterations=1))
    outcome = asyncio.run(orch.run(state, _noop_ctx))

    console.print(f"Status: [bold]{outcome.state.status.value}[/bold]")
    console.print(f"Root cause: {outcome.state.root_cause or '—'}")
    console.print(f"Stop reason: {outcome.stop_reason}")


if __name__ == "__main__":
    app()
