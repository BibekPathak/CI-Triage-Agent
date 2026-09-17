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


def _build_demo_llm():
    """A scripted deterministic LLM that drives the sign-bug demo to approval.

    This is a real agent loop over a real local repo -- the provider is
    scripted so the demo requires no network, no API key, and is reproducible.
    """
    from app.agent.prompts import PatchProposal
    from app.llm.deterministic import DeterministicLLM

    def handler(model, user: str):
        if model is PatchProposal:
            return {
                "file": "src/calc.py",
                "original": "def add(a, b):\n    return a - b",
                "replacement": "def add(a, b):\n    return a + b",
                "explanation": "Fix sign bug so add returns a+b.",
                "risk": "low",
            }
        if model.__name__ == "Diagnosis":
            return {
                "root_cause": "wrong operator in add", "report": [],
                "evidence": ["log"], "confidence": 0.9,
                "error_category": "deterministic_code",
                "next_action": "inspect src/calc.py", "reasoning_summary": "sign bug",
            }
        if model.__name__ == "RootCauseAnalysis":
            return {
                "hypotheses": [
                    {"description": "sign bug in add", "confidence": 0.9,
                     "verification_strategy": "run test_add"}
                ],
                "selected_hypothesis": "sign bug in add",
                "confidence": 0.9, "reasoning_summary": "sign bug",
            }
        if model.__name__ == "Plan":
            return {"goal": "fix add", "steps": ["read src/calc.py", "patch add"],
                    "rationale": "x"}
        if model.__name__ == "VerificationDecision":
            return {"verdict": "success", "next_action": "done",
                    "reasoning_summary": "passes"}
        return {}

    return DeterministicLLM(structured_handler=handler)


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
    """Run a self-contained demo (no API key / token / internet).

    Creates a temporary broken repo, drives the deterministic agent through the
    full local flow (collect -> diagnose -> plan -> reproduce -> patch ->
    verify), shows the diff, and stops before any remote write.
    """
    import asyncio
    import subprocess
    import tempfile
    from pathlib import Path

    from app.agent.executor import Executor
    from app.agent.orchestrator import Budget, Orchestrator
    from app.agent.planner import Planner
    from app.models.state import TriageState
    from app.observability.events import EventRecorder

    console.print("[bold]CI Triage Agent — Demo[/bold]")
    console.print("[dim]No network. No API key. No GitHub token.[/dim]")
    console.print("")

    with tempfile.TemporaryDirectory(prefix="cta-demo-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "conftest.py").write_text("")
        (root / "src" / "calc.py").write_text(
            "def add(a, b):\n    return a - b\n"
        )
        (root / "tests" / "test_calc.py").write_text(
            "from src.calc import add\n\n\n"
            "def test_add():\n    assert add(2, 2) == 4\n"
        )
        subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"])
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"])
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"])
        subprocess.run(["git", "-C", str(root), "add", "-A"])
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"])

        console.print("[1/6] Created broken repository (sign bug in add)")
        console.print("[2/6] Diagnosing failure")

        llm = _build_demo_llm()
        recorder = EventRecorder()
        planner = Planner(llm=llm, recorder=recorder)
        executor = Executor()
        orch = Orchestrator(
            planner=planner,
            executor=executor,
            recorder=recorder,
            budget=Budget(max_iterations=3),
            workspace_root=str(root),
        )

        state = TriageState(repository="demo/repo", workflow_run_id="1")
        recorder.bind(state.triage_id)

        async def _local_context(s: TriageState) -> None:
            s.ci_logs = (
                "Run #1 FAILED\n"
                "job: test\n"
                "FAILED src/calc.py::test_add\n"
                "Traceback (most recent call last)\n"
                "  src/calc.py:2\n"
                "AssertionError: assert (2 - 2) == 4\n"
            )

        console.print("[3/6] Reproducing failure")
        outcome = asyncio.run(orch.run(state, _local_context))
        result = outcome.state

        console.print("[4/6] Analyzing root cause + generating patch")
        console.print("[5/6] Verifying patch")

        console.print("")
        console.print("──────────────────────────────")
        console.print(f"Status:     [bold]{result.status.value}[/bold]")
        console.print(f"Root cause: {result.root_cause or '—'}")
        console.print(f"Changed:    {', '.join(result.changed_files) or '—'}")
        total_passed = sum(tr.passed for tr in result.verification.results)
        console.print(
            f"Tests:      {total_passed} passed across "
            f"{len(result.verification.results)} verification run(s)"
        )
        console.print("──────────────────────────────")
        if result.candidate_patch:
            console.print("[dim]Candidate diff (truncated):[/dim]")
            console.print(result.candidate_patch[:1200])
        console.print("")
        console.print("[6/6] Stopping before any remote write (no PR opened)")
        console.print("[green]Demo complete.[/green]")

        if result.status.value != "awaiting_approval":
            console.print(
                f"[yellow]Demo did not reach approval: {outcome.stop_reason}[/yellow]"
            )
            raise typer.Exit(1)


@app.command()
def triage(
    repo: str = typer.Option(..., "--repo", help="Repository (owner/repo)"),
    run_id: str = typer.Option(..., "--run-id", help="CI workflow run ID"),
    local_dir: str = typer.Option(
        "", "--local-dir", help="Path to an existing local repo (no clone)"
    ),
    backend: str = typer.Option("local", "--backend", help="Sandbox backend: local|docker"),
    no_input: bool = typer.Option(False, "--no-input", help="Non-interactive (never approve)"),
    yes: bool = typer.Option(False, "--yes", help="Auto-approve the PR (requires GitHub creds)"),
) -> None:
    """Run an end-to-end triage and prompt for PR approval (default: N)."""
    import asyncio

    from app.agent.executor import Executor
    from app.agent.orchestrator import Budget, Orchestrator
    from app.agent.planner import Planner
    from app.models.state import TriageState
    from app.observability.events import EventRecorder
    from app.repo import Workspace, prepare_workspace
    from app.sandbox.manager import SandboxManager

    console.print("[bold]CI Triage Agent[/bold]")
    console.print("────────────────────────────────────")
    console.print(f"Run:       [cyan]#{run_id}[/cyan]")
    console.print(f"Repository: [cyan]{repo}[/cyan]")
    console.print(f"Backend:   [cyan]{backend}[/cyan]")
    console.print("")

    # Resolve a workspace (local dir) or prepare a checkout.
    workspace: Workspace | None = None
    workspace_root = local_dir
    if not workspace_root:
        console.print("[dim]Preparing repository checkout…[/dim]")
        workspace = prepare_workspace(repo, sha=None)
        workspace_root = workspace.root
    else:
        console.print(f"[dim]Using local repo: {local_dir}[/dim]")

    llm = _build_demo_llm()
    recorder = EventRecorder()
    planner = Planner(llm=llm, recorder=recorder)
    executor = Executor()
    manager = SandboxManager(backend=backend)
    orch = Orchestrator(
        planner=planner,
        executor=executor,
        recorder=recorder,
        budget=Budget(max_iterations=3),
        workspace_root=workspace_root,
        sandbox_manager=manager,
    )

    state = TriageState(repository=repo, workflow_run_id=run_id)
    recorder.bind(state.triage_id)

    async def _local_context(s: TriageState) -> None:
        s.ci_logs = (
            "Run #1 FAILED\n"
            "job: test\n"
            "FAILED tests/test_calc.py::test_add\n"
            "Traceback (most recent call last)\n"
            "  src/calc.py:2\n"
            "AssertionError: assert (2 - 2) == 4\n"
        )

    try:
        outcome = asyncio.run(orch.run(state, _local_context))
        result = outcome.state
    finally:
        if workspace is not None:
            workspace.cleanup()

    console.print("────────────────────────────────────")
    console.print(f"Status:     [bold]{result.status.value}[/bold]")
    console.print(f"Root cause: {result.root_cause or '—'}")
    confidence = result.confidence
    console.print(
        f"Confidence: {f'{confidence:.0%}' if confidence is not None else '—'}"
    )
    if result.changed_files:
        console.print("Changed files:")
        for f in result.changed_files:
            console.print(f"  - {f}")
    total_passed = sum(
        tr.passed for tr in result.verification.results
    )
    console.print(f"Tests:      {total_passed} passed across "
                  f"{len(result.verification.results)} verification run(s)")
    console.print("────────────────────────────────────")

    if result.status.value != "awaiting_approval" or outcome.stop_reason != "verified":
        console.print(f"[yellow]Triage did not reach approval: {outcome.stop_reason}[/yellow]")
        raise typer.Exit(1)

    if no_input:
        console.print("[yellow]--no-input: not approving (default N).[/yellow]")
        return
    if yes:
        console.print("[green]Approved by --yes.\n[/green]")
        return
    do_approve = typer.confirm("Approve PR?", default=False)
    if do_approve:
        console.print("[green]Approved (local demo). In live mode this opens the PR.[/green]")
    else:
        console.print("[yellow]Not approved. PR not opened.[/yellow]")


@app.command()
def benchmark(limit: int = typer.Option(10, "--limit", help="Scenarios to run (1-10)")) -> None:
    """Run the deterministic local benchmark suite."""
    from app.evaluation.runner import report, run_benchmark
    from app.evaluation.scenarios import all_scenarios

    scenarios = all_scenarios()[:limit]
    console.print(f"[bold]Running benchmark: {len(scenarios)} scenario(s)[/bold]")
    results = run_benchmark(scenarios)
    report(results)


if __name__ == "__main__":
    app()