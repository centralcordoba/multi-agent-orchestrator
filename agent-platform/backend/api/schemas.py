"""
Pydantic request / response schemas for the /run endpoint.

Reference: ARCHITECTURE.md §3.2, §3.4
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="The user query to process through the agent pipeline.",
    )
    context: dict | None = Field(
        default=None,
        description="Optional context payload forwarded to agents.",
    )


# ── Per-agent trace ─────────────────────────────────────────────────────────


class AgentTrace(BaseModel):
    model_config = {"protected_namespaces": ()}

    agent: str = Field(..., description="Agent name (e.g. 'PlannerAgent').")
    output: str = Field(..., description="Agent textual output.")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Self-reported confidence."
    )
    tokens_used: int = Field(..., ge=0)
    model_used: str
    latency_ms: int = Field(..., ge=0)


# ── Aggregated metrics ──────────────────────────────────────────────────────


class RequestMetrics(BaseModel):
    total_tokens: int = Field(..., ge=0)
    total_cost_usd: float = Field(..., ge=0.0)
    total_latency_ms: int = Field(..., ge=0)


# ── Response ─────────────────────────────────────────────────────────────────


class RunResponse(BaseModel):
    result: str = Field(
        ..., description="Final aggregated answer from the pipeline."
    )
    evaluation_score: float = Field(
        ..., ge=0.0, le=1.0, description="Automated evaluation score."
    )
    agent_trace: list[AgentTrace] = Field(
        default_factory=list,
        description="Ordered execution trace of every agent.",
    )
    metrics: RequestMetrics


# ── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
