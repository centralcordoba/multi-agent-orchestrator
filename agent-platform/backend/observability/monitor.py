"""
Observability & Evaluation Layer.

Tracks per-request and per-agent metrics:
  - Token usage, estimated cost, latency
  - Model usage frequency
  - Error rate
  - Output quality evaluation

Includes integration points for LangFuse and LangSmith.

Reference: ARCHITECTURE.md §3.6, §6
"""

from __future__ import annotations

import logging
from collections import defaultdict

from pydantic import BaseModel, Field

from backend.api.schemas import AgentTrace
from backend.config.settings import Settings

logger = logging.getLogger(__name__)


# ── Metrics snapshot model ───────────────────────────────────────────────────


class AggregatedMetrics(BaseModel):
    """Read-only snapshot of cumulative runtime metrics."""

    model_config = {"protected_namespaces": ()}

    total_requests: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model_usage: dict[str, int] = Field(default_factory=dict)
    error_count: int = 0
    avg_latency_ms: float = 0.0


# ── Monitor ──────────────────────────────────────────────────────────────────


class AgentMonitor:
    """Collects, logs, and exposes agent execution metrics."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._total_requests: int = 0
        self._total_tokens: int = 0
        self._total_cost: float = 0.0
        self._model_usage: dict[str, int] = defaultdict(int)
        self._error_count: int = 0
        self._latencies: list[int] = []

        # ── External tracing backends (placeholders) ─────────────────────
        # TODO: Initialize when credentials are available:
        #   self._langfuse = langfuse.Langfuse(public_key=..., secret_key=...)
        #   self._langsmith = langsmith.Client(api_key=...)
        self._langfuse_client = None
        self._langsmith_client = None

    # ── Recording ────────────────────────────────────────────────────────

    def record_agent_call(self, trace: AgentTrace) -> None:
        """Record metrics from a single agent execution."""
        self._total_tokens += trace.tokens_used
        self._model_usage[trace.model_used] += 1
        self._latencies.append(trace.latency_ms)

        logger.info(
            "agent_call_recorded",
            extra={
                "agent": trace.agent,
                "model": trace.model_used,
                "tokens": trace.tokens_used,
                "latency_ms": trace.latency_ms,
                "confidence": trace.confidence_score,
            },
        )

        self._send_to_langfuse(trace)
        self._send_to_langsmith(trace)

    def record_request_complete(self, cost_usd: float) -> None:
        """Called once per /run request after the full pipeline finishes."""
        self._total_requests += 1
        self._total_cost += cost_usd

    def record_error(self, agent: str, error: str) -> None:
        """Track an agent-level error."""
        self._error_count += 1
        logger.error(
            "agent_error",
            extra={"agent": agent, "error": error},
        )

    # ── Evaluation (ARCHITECTURE.md §6) ──────────────────────────────────

    def evaluate_output(
        self,
        query: str,
        output: str,
        expected: str | None = None,
    ) -> float:
        """
        Score the quality of a pipeline output.

        Current implementation uses heuristic checks.
        Replace with LLM-as-judge or a trained evaluation model
        for production quality scoring.
        """
        if not output or not output.strip():
            return 0.0

        score = 0.0

        # Heuristic: non-trivial length
        if len(output) > 50:
            score += 0.3
        if len(output) > 200:
            score += 0.2

        # Heuristic: structured content (contains sentences)
        if "." in output:
            score += 0.2

        # Exact-match bonus when an expected output is provided
        if expected and expected.strip().lower() in output.strip().lower():
            score += 0.3

        return min(round(score, 2), 1.0)

    # ── Snapshot ─────────────────────────────────────────────────────────

    def get_metrics(self) -> AggregatedMetrics:
        """Return a point-in-time snapshot of accumulated metrics."""
        avg_lat = (
            sum(self._latencies) / len(self._latencies)
            if self._latencies
            else 0.0
        )
        return AggregatedMetrics(
            total_requests=self._total_requests,
            total_tokens=self._total_tokens,
            total_cost_usd=round(self._total_cost, 6),
            model_usage=dict(self._model_usage),
            error_count=self._error_count,
            avg_latency_ms=round(avg_lat, 1),
        )

    # ── External integrations (placeholders) ─────────────────────────────

    def _send_to_langfuse(self, trace: AgentTrace) -> None:
        """Forward trace to LangFuse when configured."""
        if self._langfuse_client is None:
            return
        # TODO: self._langfuse_client.trace(
        #     name=trace.agent,
        #     input={"model": trace.model_used},
        #     output={"tokens": trace.tokens_used},
        # )
        logger.debug("langfuse_trace_sent", extra={"agent": trace.agent})

    def _send_to_langsmith(self, trace: AgentTrace) -> None:
        """Forward trace to LangSmith when configured."""
        if self._langsmith_client is None:
            return
        # TODO: self._langsmith_client.create_run(
        #     name=trace.agent,
        #     run_type="chain",
        #     inputs={"model": trace.model_used},
        #     outputs={"tokens": trace.tokens_used},
        # )
        logger.debug("langsmith_trace_sent", extra={"agent": trace.agent})
