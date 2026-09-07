"""LLM-driven reasoning stages of the agent.

The :class:`Planner` wraps the LLM and turns structured model outputs into typed
domain objects and tool arguments. It is intentionally free of state-machine
control flow -- the orchestrator decides *when* to call which method.

Each method invokes the LLM through the provider abstraction (deterministic in
tests/demo) and records a concise reasoning trace event.
"""

from __future__ import annotations

from app.agent import prompts
from app.agent.parser import parse_ci_logs
from app.models.domain import Hypothesis, PlanStep, TestResult
from app.models.state import TriageState
from app.observability.events import EventRecorder, RunEvent


class Planner:
    """Encapsulates the LLM reasoning steps of a triage run."""

    def __init__(self, llm, recorder: EventRecorder | None = None) -> None:
        self.llm = llm
        self.recorder = recorder or EventRecorder()

    async def analyze_failure(self, state: TriageState) -> prompts.Diagnosis:
        """Classify + summarize the raw CI failure."""
        failure = parse_ci_logs(state.ci_logs or "")
        state.failure = failure
        diag = await self.llm.structured(
            prompts.Diagnosis,
            prompts.SYSTEM_FAILURE_ANALYSIS,
            prompts.user_failure_analysis(state.ci_logs or "", str(failure.model_dump())),
            cost_sink=state.record_cost,
        )
        if diag.confidence > 0:
            state.confidence = diag.confidence
        if diag.root_cause and not state.root_cause:
            state.root_cause = diag.root_cause
        self.recorder.record(
            RunEvent(
                step="analyze_failure",
                phase="DIAGNOSE",
                reasoning_summary=diag.reasoning_summary,
                decision=diag.next_action,
                evidence="; ".join(diag.evidence),
                result={"root_cause": diag.root_cause, "confidence": diag.confidence,
                        "error_category": diag.error_category},
            )
        )
        return diag

    async def generate_hypotheses(self, state: TriageState) -> list[Hypothesis]:
        """Generate + select competing hypotheses for the failure."""
        rca = await self.llm.structured(
            prompts.RootCauseAnalysis,
            prompts.SYSTEM_ROOT_CAUSE,
            prompts.user_root_cause(
                str(state.failure.model_dump()),
                state.repository_context.get("summary", ""),
            ),
            cost_sink=state.record_cost,
        )
        selected = rca.selected_hypothesis
        hypotheses: list[Hypothesis] = []
        for raw in rca.hypotheses:
            h = Hypothesis(
                description=raw.get("description", ""),
                evidence=raw.get("evidence", []),
                confidence=float(raw.get("confidence", 0.0)),
                verification_strategy=raw.get("verification_strategy", ""),
            )
            hypotheses.append(h)
        state.hypotheses = hypotheses
        # pick selected (by description match) or highest confidence
        chosen = next((h for h in hypotheses if h.description == selected), None)
        if chosen is None and hypotheses:
            chosen = max(hypotheses, key=lambda h: h.confidence)
        if chosen:
            state.selected_hypothesis = chosen.id
        self.recorder.record(
            RunEvent(
                step="generate_hypotheses",
                phase="DIAGNOSE",
                reasoning_summary=rca.reasoning_summary,
                decision=selected,
                result={"count": len(hypotheses), "selected": selected},
            )
        )
        return hypotheses

    async def build_plan(self, state: TriageState, diagnosis_summary: str) -> list[PlanStep]:
        """Produce an ordered debugging plan from the diagnosis."""
        plan_out = await self.llm.structured(
            prompts.Plan,
            prompts.SYSTEM_PLANNING,
            prompts.user_planning(diagnosis_summary, str(state.as_digest())),
            cost_sink=state.record_cost,
        )
        steps = [PlanStep(description=s) for s in plan_out.steps]
        state.plan = steps
        self.recorder.record(
            RunEvent(
                step="build_plan",
                phase="PLAN",
                reasoning_summary=plan_out.rationale,
                decision=f"plan:{len(steps)} steps",
                result={"goal": plan_out.goal, "steps": len(steps)},
            )
        )
        return steps

    async def propose_patch(
        self, state: TriageState, file_path: str, file_content: str
    ) -> prompts.PatchProposal:
        """Generate a candidate patch for a single file."""
        hypothesis_text = ""
        chosen = next((h for h in state.hypotheses if h.id == state.selected_hypothesis), None)
        if chosen:
            hypothesis_text = chosen.description
        proposal = await self.llm.structured(
            prompts.PatchProposal,
            prompts.SYSTEM_PATCH,
            prompts.user_patch(
                file_path, file_content, str(state.failure.model_dump()), hypothesis_text
            ),
            cost_sink=state.record_cost,
        )
        self.recorder.record(
            RunEvent(
                step="propose_patch",
                phase="PATCH",
                reasoning_summary=proposal.explanation,
                decision=f"patch:{file_path}",
                result={"file": proposal.file, "risk": proposal.risk},
            )
        )
        return proposal

    async def decide_verification(
        self, results: list[TestResult], changed_files: list[str]
    ) -> prompts.VerificationDecision:
        """Evaluate test results and choose next step if the fix fails."""
        decision = await self.llm.structured(
            prompts.VerificationDecision,
            prompts.SYSTEM_VERIFY,
            prompts.user_verify(results, ", ".join(changed_files)),
            cost_sink=None,  # no cost sink: called many times, negligible
        )
        return decision
