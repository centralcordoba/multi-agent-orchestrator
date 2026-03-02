"""
API route handlers.

All business / agent logic is delegated to the orchestration layer.
Route handlers only validate input, call orchestration, and return output.

Reference: ARCHITECTURE.md §3.2 — "No agent logic inside route handlers."
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.agents.integration import get_default_agents
from backend.api.schemas import HealthResponse, RunRequest, RunResponse
from backend.config.settings import Settings, get_settings
from backend.observability.monitor import AggregatedMetrics, AgentMonitor
from backend.orchestration.planner import WorkflowOrchestrator, create_orchestrator

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Dependency injection ─────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]

_orchestrator: WorkflowOrchestrator | None = None
_monitor: AgentMonitor | None = None


def get_orchestrator(settings: SettingsDep) -> WorkflowOrchestrator:
    """Lazy-singleton orchestrator wired with default agents."""
    global _orchestrator, _monitor
    if _orchestrator is None:
        _orchestrator = create_orchestrator(settings, get_default_agents())
        _monitor = _orchestrator._monitor
    return _orchestrator


def get_monitor() -> AgentMonitor:
    """Return the shared monitor instance (requires orchestrator init)."""
    if _monitor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitor not initialised — call /run first.",
        )
    return _monitor


OrchestratorDep = Annotated[WorkflowOrchestrator, Depends(get_orchestrator)]
MonitorDep = Annotated[AgentMonitor, Depends(get_monitor)]


# ── Health ───────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
)
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(version=settings.app_version)


# ── Run pipeline ─────────────────────────────────────────────────────────────


@router.post(
    "/run",
    response_model=RunResponse,
    status_code=status.HTTP_200_OK,
    tags=["orchestration"],
    summary="Execute the full agent pipeline",
)
async def run_pipeline(
    body: RunRequest,
    orchestrator: OrchestratorDep,
) -> RunResponse:
    """
    Accepts a user query, delegates to the orchestration workflow,
    and returns the aggregated result with full execution trace.
    """
    logger.info(
        "pipeline_start",
        extra={
            "query_length": len(body.query),
            "has_context": body.context is not None,
        },
    )

    result = await orchestrator.execute(body)

    logger.info(
        "pipeline_end",
        extra={
            "total_tokens": result.metrics.total_tokens,
            "total_cost_usd": result.metrics.total_cost_usd,
            "evaluation_score": result.evaluation_score,
        },
    )

    return result


# ── Metrics ──────────────────────────────────────────────────────────────────


@router.get(
    "/metrics",
    response_model=AggregatedMetrics,
    tags=["observability"],
    summary="Aggregated runtime metrics",
)
async def metrics(monitor: MonitorDep) -> AggregatedMetrics:
    """Return a snapshot of accumulated agent and request metrics."""
    return monitor.get_metrics()
