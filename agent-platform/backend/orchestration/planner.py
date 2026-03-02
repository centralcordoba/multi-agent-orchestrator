"""
Agent Orchestration Layer.

Coordinates the multi-agent pipeline in strict sequential order:

  PlannerAgent → ResearchAgent → AnalystAgent → ReviewerAgent

Each stage enriches a shared WorkflowState object.
The orchestrator enforces cost caps and logs every transition.

Designed for easy swap-in of CrewAI, LangGraph, or AutoGen
by replacing the ``_run_stage`` internals.

Reference: ARCHITECTURE.md §3.3, §5
"""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel, Field

from backend.agents.integration import AgentInput, AgentOutput, BaseAgent
from backend.api.schemas import AgentTrace, RequestMetrics, RunRequest, RunResponse
from backend.config.settings import Settings
from backend.observability.monitor import AgentMonitor
from backend.routing.router import (
    AgentRouter,
    AgentStage,
    RoutingContext,
    TaskComplexity,
)

logger = logging.getLogger(__name__)


# ── Shared pipeline state ───────────────────────────────────────────────────


class WorkflowState(BaseModel):
    """Mutable state object passed through every agent stage."""

    query: str
    context: dict = Field(default_factory=dict)
    plan: str = ""
    research: str = ""
    analysis: str = ""
    review: str = ""
    agent_traces: list[AgentTrace] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0


# ── Pipeline definition ─────────────────────────────────────────────────────

PIPELINE: list[tuple[str, AgentStage]] = [
    ("PlannerAgent", AgentStage.PLANNER),
    ("ResearchAgent", AgentStage.RESEARCHER),
    ("AnalystAgent", AgentStage.ANALYST),
    ("ReviewerAgent", AgentStage.REVIEWER),
]

_STAGE_TO_FIELD: dict[AgentStage, str] = {
    AgentStage.PLANNER: "plan",
    AgentStage.RESEARCHER: "research",
    AgentStage.ANALYST: "analysis",
    AgentStage.REVIEWER: "review",
}


# ── Orchestrator ─────────────────────────────────────────────────────────────


class WorkflowOrchestrator:
    """Executes the ordered agent pipeline and aggregates results."""

    def __init__(
        self,
        settings: Settings,
        router: AgentRouter,
        monitor: AgentMonitor,
        agents: dict[str, BaseAgent] | None = None,
    ) -> None:
        self._settings = settings
        self._router = router
        self._monitor = monitor
        self._agents: dict[str, BaseAgent] = agents or {}

    # ── Agent registration ───────────────────────────────────────────────

    def register_agent(self, name: str, agent: BaseAgent) -> None:
        """Register (or replace) a named agent in the pipeline."""
        self._agents[name] = agent

    def register_agents(self, agents: dict[str, BaseAgent]) -> None:
        """Bulk-register agents."""
        self._agents.update(agents)

    # ── Main execution ───────────────────────────────────────────────────

    async def execute(self, request: RunRequest) -> RunResponse:
        """
        Run the full pipeline and return a structured response.

        Integration points for alternative frameworks:
          - CrewAI:    replace the for-loop with a Crew(...).kickoff()
          - LangGraph: replace with a compiled StateGraph.ainvoke()
          - AutoGen:   replace with a GroupChat.arun()
        """
        pipeline_start = time.perf_counter()

        state = WorkflowState(
            query=request.query,
            context=request.context or {},
        )

        logger.info(
            "workflow_start",
            extra={"query_length": len(request.query)},
        )

        for agent_name, stage in PIPELINE:
            state = await self._run_stage(agent_name, stage, state)

            # Cost guardrail (ARCHITECTURE.md §8)
            if state.total_cost_usd >= self._settings.max_cost_per_request:
                logger.warning(
                    "cost_cap_reached",
                    extra={
                        "spent": state.total_cost_usd,
                        "cap": self._settings.max_cost_per_request,
                    },
                )
                break

        total_latency = int((time.perf_counter() - pipeline_start) * 1000)

        # Evaluate final output (ARCHITECTURE.md §6)
        final_output = (
            state.review
            or state.analysis
            or state.research
            or state.plan
        )
        evaluation = self._monitor.evaluate_output(
            query=state.query,
            output=final_output,
        )

        # Record request-level metrics
        self._monitor.record_request_complete(cost_usd=state.total_cost_usd)

        logger.info(
            "workflow_complete",
            extra={
                "total_latency_ms": total_latency,
                "evaluation_score": evaluation,
                "total_tokens": state.total_tokens,
            },
        )

        return RunResponse(
            result=final_output or "No output produced.",
            evaluation_score=evaluation,
            agent_trace=state.agent_traces,
            metrics=RequestMetrics(
                total_tokens=state.total_tokens,
                total_cost_usd=round(state.total_cost_usd, 6),
                total_latency_ms=total_latency,
            ),
        )

    # ── Single stage execution ───────────────────────────────────────────

    async def _run_stage(
        self,
        agent_name: str,
        stage: AgentStage,
        state: WorkflowState,
    ) -> WorkflowState:
        """Execute one agent stage and update the shared state."""

        # 1. Route — determine which model to use
        routing = self._router.route(
            RoutingContext(
                task_complexity=TaskComplexity.MEDIUM,
                stage=stage,
                token_estimate=len(state.query.split()) * 2,
            )
        )

        # 2. Build agent input from accumulated state
        previous = state.plan or state.research or state.analysis or ""
        agent_input = AgentInput(
            query=state.query,
            context=state.context,
            previous_output=previous,
            model=routing.model,
            max_tokens=routing.max_tokens,
        )

        # 3. Execute
        stage_start = time.perf_counter()
        agent = self._agents.get(agent_name)

        if agent is None:
            logger.warning(
                "agent_not_registered",
                extra={"agent": agent_name},
            )
            output = AgentOutput(
                agent=agent_name,
                output=f"[{agent_name}] placeholder — agent not implemented.",
                confidence_score=0.0,
                tokens_used=0,
                model_used=routing.model,
                latency_ms=0,
            )
        else:
            try:
                output = await agent.run(agent_input)
            except Exception as exc:
                self._monitor.record_error(agent_name, str(exc))
                output = AgentOutput(
                    agent=agent_name,
                    output=f"[{agent_name}] failed: {exc}",
                    confidence_score=0.0,
                    tokens_used=0,
                    model_used=routing.model,
                    latency_ms=0,
                )

        output.latency_ms = int((time.perf_counter() - stage_start) * 1000)

        # 4. Record trace
        trace = AgentTrace(
            agent=output.agent,
            output=output.output,
            confidence_score=output.confidence_score,
            tokens_used=output.tokens_used,
            model_used=output.model_used,
            latency_ms=output.latency_ms,
        )
        state.agent_traces.append(trace)
        state.total_tokens += output.tokens_used
        state.total_cost_usd += routing.estimated_cost_usd

        # 5. Enrich shared state
        setattr(state, _STAGE_TO_FIELD[stage], output.output)

        # 6. Notify monitor
        self._monitor.record_agent_call(trace)

        return state


# ── Convenience factory ──────────────────────────────────────────────────────


def create_orchestrator(
    settings: Settings,
    agents: dict[str, BaseAgent] | None = None,
) -> WorkflowOrchestrator:
    """Build a fully wired orchestrator with the default agents."""
    router = AgentRouter(settings)
    monitor = AgentMonitor(settings)
    orchestrator = WorkflowOrchestrator(settings, router, monitor, agents)
    return orchestrator
