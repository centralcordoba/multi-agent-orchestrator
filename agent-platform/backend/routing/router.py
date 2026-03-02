"""
LLM Routing Layer.

Selects the appropriate LLM model based on:
  - Task complexity  (low / medium / high)
  - Pipeline stage   (planner / researcher / analyst / reviewer)
  - Estimated token count

All routing decisions are logged as structured JSON.

Reference: ARCHITECTURE.md §3.5
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from backend.config.settings import Settings

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class TaskComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentStage(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    REVIEWER = "reviewer"


# ── Models ───────────────────────────────────────────────────────────────────


class RoutingContext(BaseModel):
    """Input context used to make a routing decision."""

    task_complexity: TaskComplexity = TaskComplexity.MEDIUM
    stage: AgentStage
    token_estimate: int = Field(default=0, ge=0)


class RoutingDecision(BaseModel):
    """Result returned after evaluating routing rules."""

    model: str
    max_tokens: int
    estimated_cost_usd: float = Field(ge=0.0)
    reason: str
    fallback_model: str | None = None


# ── Approximate cost rates (per 1 000 tokens) ───────────────────────────────

_COST_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.000_15, "output": 0.000_6},
    "gpt-4o":      {"input": 0.002_5,  "output": 0.010},
}


# ── Router ───────────────────────────────────────────────────────────────────


class AgentRouter:
    """Applies rule-based logic to select an LLM model for a given context."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._low_cost = settings.default_low_cost_model
        self._high_reasoning = settings.default_high_reasoning_model
        self._max_tokens = settings.max_tokens_per_agent

    # ── Public API ───────────────────────────────────────────────────────

    def route(self, ctx: RoutingContext) -> RoutingDecision:
        """Evaluate routing rules and return a model selection decision."""
        model = self._select_model(ctx)
        fallback = self._select_fallback(model)
        cost = self._estimate_cost(model, ctx.token_estimate)

        decision = RoutingDecision(
            model=model,
            max_tokens=self._max_tokens,
            estimated_cost_usd=cost,
            reason=self._explain(ctx, model),
            fallback_model=fallback,
        )

        logger.info(
            "routing_decision",
            extra={
                "stage": ctx.stage.value,
                "complexity": ctx.task_complexity.value,
                "model": decision.model,
                "estimated_cost_usd": decision.estimated_cost_usd,
            },
        )

        return decision

    # ── Selection rules (ARCHITECTURE.md §3.5) ──────────────────────────

    def _select_model(self, ctx: RoutingContext) -> str:
        # Rule 1: reviewer always gets the high-reasoning model
        if ctx.stage == AgentStage.REVIEWER:
            return self._high_reasoning

        # Rule 2: high complexity → high-reasoning model
        if ctx.task_complexity == TaskComplexity.HIGH:
            return self._high_reasoning

        # Rule 3: low complexity → low-cost model
        if ctx.task_complexity == TaskComplexity.LOW:
            return self._low_cost

        # Rule 4: token estimate exceeds limit → low-cost to save budget
        if ctx.token_estimate > self._max_tokens:
            return self._low_cost

        # Default for medium complexity
        return self._low_cost

    def _select_fallback(self, primary: str) -> str | None:
        """Return the opposite-tier model as fallback."""
        if primary == self._high_reasoning:
            return self._low_cost
        return self._high_reasoning

    # ── Cost estimation ──────────────────────────────────────────────────

    def _estimate_cost(self, model: str, token_estimate: int) -> float:
        rates = _COST_PER_1K.get(model, _COST_PER_1K.get(self._low_cost, {}))
        if not rates:
            return 0.0
        input_cost = (token_estimate / 1_000) * rates["input"]
        output_cost = (self._max_tokens / 1_000) * rates["output"]
        return round(input_cost + output_cost, 6)

    # ── Explainability ───────────────────────────────────────────────────

    @staticmethod
    def _explain(ctx: RoutingContext, model: str) -> str:
        parts = [
            f"stage={ctx.stage.value}",
            f"complexity={ctx.task_complexity.value}",
        ]
        if ctx.token_estimate > 0:
            parts.append(f"token_estimate={ctx.token_estimate}")
        return f"Selected {model}: {', '.join(parts)}"
