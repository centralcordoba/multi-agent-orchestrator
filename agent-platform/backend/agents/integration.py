"""
Reusable Agent Templates & Integration Helpers.

Provides:
  - BaseAgent          – abstract base class all pipeline agents extend
  - AgentInput/Output  – canonical I/O Pydantic models
  - ExternalServiceConnector – async helpers for DB / API calls
  - Concrete placeholder agents for every pipeline stage

New agent types are added by subclassing BaseAgent and implementing
the ``execute`` method.

Reference: ARCHITECTURE.md §3.4, §3.8
"""

from __future__ import annotations

import abc
import logging
import time

from pydantic import BaseModel, Field

from backend.config.settings import Settings

logger = logging.getLogger(__name__)


# ── Agent I/O models ────────────────────────────────────────────────────────


class AgentInput(BaseModel):
    """Structured input every agent receives."""

    query: str
    context: dict = Field(default_factory=dict)
    previous_output: str = ""
    model: str = ""
    max_tokens: int = 4096


class AgentOutput(BaseModel):
    """Structured output every agent must return."""

    model_config = {"protected_namespaces": ()}

    agent: str
    output: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    tokens_used: int = Field(default=0, ge=0)
    model_used: str = ""
    latency_ms: int = Field(default=0, ge=0)


# ── Base Agent ──────────────────────────────────────────────────────────────


class BaseAgent(abc.ABC):
    """
    Abstract base for all pipeline agents.

    Subclasses implement ``execute()`` with their core logic.
    The ``run()`` wrapper adds timing, structured logging, and
    error handling automatically.
    """

    name: str = "BaseAgent"

    @abc.abstractmethod
    async def execute(self, inp: AgentInput) -> str:
        """Core agent logic — subclasses must implement this."""

    async def run(self, inp: AgentInput) -> AgentOutput:
        """Execute with timing, logging, and error handling."""
        start = time.perf_counter()

        try:
            result = await self.execute(inp)
            confidence = self._default_confidence()
        except Exception as exc:
            logger.error(
                "agent_execution_failed",
                extra={"agent": self.name, "error": str(exc)},
            )
            result = f"Error in {self.name}: {exc}"
            confidence = 0.0

        latency = int((time.perf_counter() - start) * 1000)

        return AgentOutput(
            agent=self.name,
            output=result,
            confidence_score=confidence,
            tokens_used=0,  # overridden when a real LLM call is made
            model_used=inp.model,
            latency_ms=latency,
        )

    def _default_confidence(self) -> float:
        """Override per agent to provide a better default."""
        return 0.85


# ── Placeholder Agents ──────────────────────────────────────────────────────
# Each mirrors a pipeline stage from ARCHITECTURE.md §3.3.
# Replace the ``execute`` body with real LLM / tool calls.


class PlannerAgent(BaseAgent):
    """Decomposes a user query into an execution plan."""

    name = "PlannerAgent"

    async def execute(self, inp: AgentInput) -> str:
        # TODO: Call LLM via routing layer to generate a plan.
        logger.info("planner_execute", extra={"query_len": len(inp.query)})
        return f"[Plan] Decomposed query into actionable steps for: {inp.query[:120]}"


class ResearchAgent(BaseAgent):
    """Gathers information relevant to the plan."""

    name = "ResearchAgent"

    async def execute(self, inp: AgentInput) -> str:
        # TODO: Call LLM + optional external data sources.
        logger.info("researcher_execute", extra={"prev_len": len(inp.previous_output)})
        return f"[Research] Gathered context based on plan: {inp.previous_output[:120]}"


class AnalystAgent(BaseAgent):
    """Synthesises research into a structured analysis."""

    name = "AnalystAgent"

    async def execute(self, inp: AgentInput) -> str:
        # TODO: Call LLM with research context.
        logger.info("analyst_execute", extra={"prev_len": len(inp.previous_output)})
        return f"[Analysis] Synthesised findings: {inp.previous_output[:120]}"


class ReviewerAgent(BaseAgent):
    """Reviews and refines the analysis for final delivery."""

    name = "ReviewerAgent"

    def _default_confidence(self) -> float:
        return 0.90  # reviewer is the final gate

    async def execute(self, inp: AgentInput) -> str:
        # TODO: Call high-reasoning LLM to review quality.
        logger.info("reviewer_execute", extra={"prev_len": len(inp.previous_output)})
        return f"[Review] Final verified output: {inp.previous_output[:120]}"


# ── External Service Connector ──────────────────────────────────────────────


class ExternalServiceConnector:
    """
    Async helpers for calling external systems (REST APIs, databases).

    Agents that need external data should receive an instance via
    dependency injection rather than creating their own.

    Reference: ARCHITECTURE.md §3.8
    """

    def __init__(self, base_url: str = "", timeout: int = 30) -> None:
        self._base_url = base_url
        self._timeout = timeout

    async def fetch_json(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> dict:
        """GET JSON from an external API."""
        import httpx

        url = f"{self._base_url}{endpoint}"
        logger.info("external_fetch", extra={"url": url})

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def post_json(
        self,
        endpoint: str,
        payload: dict,
    ) -> dict:
        """POST JSON to an external API."""
        import httpx

        url = f"{self._base_url}{endpoint}"
        logger.info("external_post", extra={"url": url})

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def query_database(
        self,
        query: str,
        params: dict | None = None,
    ) -> list[dict]:
        """
        Execute a database query.

        TODO: Replace with a real async DB driver (asyncpg, motor, etc.).
        """
        logger.info("db_query", extra={"query_preview": query[:80]})
        return []


# ── Agent registry helper ───────────────────────────────────────────────────


def get_default_agents() -> dict[str, BaseAgent]:
    """Return the standard four-stage agent set."""
    return {
        "PlannerAgent": PlannerAgent(),
        "ResearchAgent": ResearchAgent(),
        "AnalystAgent": AnalystAgent(),
        "ReviewerAgent": ReviewerAgent(),
    }
