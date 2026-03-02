# Technical Architecture — Implementation Detail

**Project:** Enterprise Agentic AI Platform
**Version:** 0.1.0
**Author:** Emanuel Jiménez
**Date:** March 2026
**Status:** Foundation complete — agents wired with placeholder logic

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Layer-by-Layer Breakdown](#2-layer-by-layer-breakdown)
   - 2.1 [Entry Point — main.py](#21-entry-point--mainpy)
   - 2.2 [Configuration — config/settings.py](#22-configuration--configsettingspy)
   - 2.3 [API Layer — api/routes.py & api/schemas.py](#23-api-layer--apiroutespy--apischemaspy)
   - 2.4 [LLM Routing — routing/router.py](#24-llm-routing--routingrouterpy)
   - 2.5 [Observability — observability/monitor.py](#25-observability--observabilitymonitorpy)
   - 2.6 [Agent Templates — agents/integration.py](#26-agent-templates--agentsintegrationpy)
   - 2.7 [Orchestration — orchestration/planner.py](#27-orchestration--orchestrationplannerpy)
3. [Design Patterns & Justifications](#3-design-patterns--justifications)
4. [Dependency Graph](#4-dependency-graph)
5. [Request Lifecycle (Detailed)](#5-request-lifecycle-detailed)
6. [Cost Control Implementation](#6-cost-control-implementation)
7. [Alternatives Considered](#7-alternatives-considered)
8. [Current Limitations & Next Steps](#8-current-limitations--next-steps)

---

## 1. Executive Summary

The platform is a **modular, config-driven, observable** multi-agent
orchestration system built on FastAPI. It processes user queries through a
four-stage sequential pipeline:

```
PlannerAgent → ResearchAgent → AnalystAgent → ReviewerAgent
```

Each stage enriches a shared `WorkflowState` object. Every LLM call is
routed through a rule-based engine that selects the appropriate model,
estimates cost, and logs the decision. An observability layer tracks
tokens, latency, model usage, and errors across the full request
lifecycle.

### Key architectural decisions

| Decision | Rationale |
|----------|-----------|
| FastAPI + Pydantic | Automatic validation, OpenAPI docs, async-native |
| App factory pattern | Testability — each test can create a fresh app |
| Lazy-singleton orchestrator | Avoid startup cost; share state across requests |
| Rule-based routing (not ML) | Predictable, auditable, zero additional cost |
| In-memory metrics | Sufficient for MVP; swappable for Prometheus/Redis |
| Abstract base agent class | New agents added by implementing one method |

---

## 2. Layer-by-Layer Breakdown

### 2.1 Entry Point — `main.py`

**Location:** `backend/main.py`
**Lines of code:** ~50
**Principle enforced:** *No business logic inside main.py* (ARCHITECTURE.md §2.1)

#### What it does

1. **Configures structured JSON logging** to stdout via `_configure_logging()`.
2. **Creates the FastAPI app** via the `create_app()` factory function.
3. **Attaches middleware:** CORS (configurable origins) and a request-ID
   middleware that propagates `X-Request-ID` headers.
4. **Mounts the API router** at the `/api` prefix.

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **App factory (`create_app()`)** | Enables isolated test instances, avoids module-level side effects | Module-level `app = FastAPI()` — simpler but harder to test |
| **Lifespan context manager** | Modern FastAPI pattern (replaces deprecated `@app.on_event`) | `startup`/`shutdown` events — deprecated since FastAPI 0.93 |
| **JSON log format** | Machine-parseable, compatible with ELK/Datadog/CloudWatch | `structlog` — more features but adds a dependency |
| **Request-ID middleware** | Enables end-to-end request tracing across logs | OpenTelemetry — more powerful but heavier for an MVP |
| **CORS via settings** | Frontend origin configurable per environment | Hardcoded origins — inflexible across dev/staging/prod |

#### What it intentionally does NOT do

- Import or reference any agent, orchestration, or routing module.
- Contain any `try/except` business logic.
- Define any Pydantic models.

---

### 2.2 Configuration — `config/settings.py`

**Location:** `backend/config/settings.py`
**Lines of code:** ~45

#### What it does

Defines a single `Settings` class that inherits from `pydantic_settings.BaseSettings`.
Every field is overridable via environment variable or `.env` file.

```
app_name, app_version, debug, log_level
default_low_cost_model, default_high_reasoning_model
max_tokens_per_agent, max_cost_per_request, request_timeout_seconds
cors_origins
```

A `get_settings()` function returns a singleton instance via `@lru_cache(maxsize=1)`.

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **`pydantic-settings`** | Type-safe, validates on startup, auto-loads `.env` | `python-dotenv` + `os.getenv()` — no validation, no type coercion |
| **`@lru_cache` singleton** | Zero-cost after first call; no global mutable state | Module-level instance — risks import-order bugs in tests |
| **Flat field list** | Simple, grep-able, one source of truth | Nested config objects — unnecessary complexity at this scale |
| **`.env` file path relative to `backend/`** | Works regardless of working directory | Hardcoded absolute path — breaks across machines |

#### Configurable values mapped to ARCHITECTURE.md §3.7

| Setting | Default | ARCHITECTURE.md reference |
|---------|---------|--------------------------|
| `max_tokens_per_agent` | 4096 | MAX_TOKENS_PER_AGENT |
| `max_cost_per_request` | 0.50 | MAX_COST_PER_REQUEST |
| `default_low_cost_model` | gpt-4o-mini | DEFAULT_LOW_COST_MODEL |
| `default_high_reasoning_model` | gpt-4o | DEFAULT_HIGH_REASONING_MODEL |

---

### 2.3 API Layer — `api/routes.py` & `api/schemas.py`

**Location:** `backend/api/routes.py`, `backend/api/schemas.py`
**Principle enforced:** *No agent logic inside route handlers* (ARCHITECTURE.md §3.2)

#### Endpoints

| Method | Path | Purpose | Response model |
|--------|------|---------|---------------|
| `GET` | `/api/health` | Liveness check | `HealthResponse` |
| `POST` | `/api/run` | Execute agent pipeline | `RunResponse` |
| `GET` | `/api/metrics` | Runtime observability | `AggregatedMetrics` |

#### Schemas

```
RunRequest
├── query: str (1..10 000 chars)
└── context: dict | None

RunResponse
├── result: str
├── evaluation_score: float (0.0–1.0)
├── agent_trace: list[AgentTrace]
│   ├── agent: str
│   ├── output: str
│   ├── confidence_score: float
│   ├── tokens_used: int
│   ├── model_used: str
│   └── latency_ms: int
└── metrics: RequestMetrics
    ├── total_tokens: int
    ├── total_cost_usd: float
    └── total_latency_ms: int
```

#### Dependency injection

```python
SettingsDep      = Annotated[Settings, Depends(get_settings)]
OrchestratorDep  = Annotated[WorkflowOrchestrator, Depends(get_orchestrator)]
MonitorDep       = Annotated[AgentMonitor, Depends(get_monitor)]
```

`get_orchestrator()` is a **lazy singleton**: it creates the orchestrator
(with router, monitor, and default agents) on the first request, then
reuses it for all subsequent requests.

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **`Annotated[T, Depends()]` style** | Type-safe, IDE-friendly, clean function signatures | Positional `Depends()` — less readable |
| **Lazy singleton** | Avoids slow startup; initializes only when `/run` is first called | Eager init in `lifespan` — fine, but couples `main.py` to orchestration |
| **Separate schemas file** | Keeps route handlers short; schemas reusable across layers | Inline models in routes — clutters the file |
| **`/api` prefix on router** | Leaves room for future WebSocket or static file mounts at `/` | No prefix — works but risks path collisions |
| **`/metrics` endpoint** | Enables frontend dashboards and external monitoring | Prometheus `/metrics` scrape endpoint — better for production, heavier |

---

### 2.4 LLM Routing — `routing/router.py`

**Location:** `backend/routing/router.py`
**Lines of code:** ~120
**Principle enforced:** *All routing decisions must be logged* (ARCHITECTURE.md §3.5)

#### Core concept

The `AgentRouter` class receives a `RoutingContext` (stage, complexity,
token estimate) and returns a `RoutingDecision` (model, max tokens,
estimated cost, human-readable reason, fallback model).

#### Routing rules (in priority order)

| # | Condition | Model selected | Rationale |
|---|-----------|---------------|-----------|
| 1 | `stage == REVIEWER` | high-reasoning | Final quality gate requires best model |
| 2 | `complexity == HIGH` | high-reasoning | Complex tasks need stronger reasoning |
| 3 | `complexity == LOW` | low-cost | Save budget on simple tasks |
| 4 | `token_estimate > max_tokens` | low-cost | Avoid blowing budget on large contexts |
| 5 | Default (MEDIUM) | low-cost | Conservative default minimizes spend |

#### Cost estimation

```
input_cost  = (token_estimate / 1000) × input_rate
output_cost = (max_tokens     / 1000) × output_rate
total       = input_cost + output_cost
```

Rates stored in `_COST_PER_1K` dict, currently:

| Model | Input (per 1K) | Output (per 1K) |
|-------|---------------|-----------------|
| gpt-4o-mini | $0.000_15 | $0.000_6 |
| gpt-4o | $0.002_5 | $0.010 |

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **Rule-based routing** | Deterministic, auditable, zero latency | ML classifier — adds complexity, needs training data |
| **Enums for stages/complexity** | Type-safe, exhaustive matching | Raw strings — typo-prone, no IDE autocomplete |
| **Explainability (`reason` field)** | Every decision traceable in logs and API response | Silent routing — harder to debug cost anomalies |
| **Fallback model** | Graceful degradation if primary model is unavailable | No fallback — single point of failure |
| **Static cost map** | Simple, correct for known models | Live API price lookup — adds latency and external dependency |

#### Alternatives considered

1. **Semantic router (embeddings-based):** Route based on query similarity
   to predefined intents. Rejected for MVP — adds embedding model cost and
   latency, better suited for 10+ agent scenarios.

2. **LLM-as-router:** Use a cheap model to classify then route. Rejected —
   adds a latency hop and token cost for every request.

3. **Weight-based load balancing:** Distribute across providers (OpenAI,
   Anthropic, Mistral). Planned for future; current architecture supports
   it by extending `_select_model()`.

---

### 2.5 Observability — `observability/monitor.py`

**Location:** `backend/observability/monitor.py`
**Lines of code:** ~130
**Principle enforced:** *Observability First* (ARCHITECTURE.md §2.4)

#### What it tracks

| Metric | Granularity | Storage |
|--------|-------------|---------|
| Tokens used | Per agent call | In-memory counter |
| Model usage count | Per model | `defaultdict(int)` |
| Latency | Per agent call | List (for avg calculation) |
| Error count | Global | Integer counter |
| Total cost | Per request | Float accumulator |
| Total requests | Global | Integer counter |

#### Evaluation engine

`evaluate_output()` produces a quality score (0.0–1.0) using heuristic rules:

| Criterion | Score contribution |
|-----------|--------------------|
| Output length > 50 chars | +0.3 |
| Output length > 200 chars | +0.2 |
| Contains sentences (has `.`) | +0.2 |
| Matches expected output (if provided) | +0.3 |

This is a **placeholder evaluator**. Production replacements:

| Option | Pros | Cons |
|--------|------|------|
| LLM-as-judge | High accuracy, nuanced | Adds LLM cost per evaluation |
| Trained classifier | Fast, cheap at inference | Needs labelled dataset |
| RAGAS / DeepEval | Framework support, metrics suite | Dependency overhead |
| Human-in-the-loop | Ground truth | Does not scale |

#### External integration hooks

The monitor has placeholder methods for:

- **LangFuse** (`_send_to_langfuse`): Open-source LLM observability.
  Recommended for self-hosted deployments.
- **LangSmith** (`_send_to_langsmith`): LangChain's managed tracing.
  Recommended if already using LangChain.

Both are no-ops until credentials are configured.

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **In-memory metrics** | Zero dependencies, instant reads, sufficient for single-process MVP | Redis/Prometheus — production-ready but adds infra |
| **Heuristic evaluation** | No cost, instant, good enough for development | LLM-as-judge — accurate but expensive |
| **Pluggable tracing backends** | Supports LangFuse and LangSmith without hard dependency | OpenTelemetry SDK — more standard, but heavier setup |
| **`get_metrics()` snapshot** | Thread-safe read; no locks needed for single-process | Streaming metrics via SSE — better for live dashboards |

#### Alternatives considered

1. **Prometheus client library:** Industry standard for metrics. Rejected
   for MVP — requires a Prometheus server. Can be added later via the
   existing `get_metrics()` snapshot.

2. **OpenTelemetry:** Full distributed tracing. Planned for production.
   Current request-ID middleware is a stepping stone.

3. **Database-backed metrics:** Write every trace to PostgreSQL. Rejected —
   adds write latency to every agent call. Better to batch-flush
   asynchronously.

---

### 2.6 Agent Templates — `agents/integration.py`

**Location:** `backend/agents/integration.py`
**Lines of code:** ~180
**Principle enforced:** *Stateless Agents* (ARCHITECTURE.md §2.3)

#### Class hierarchy

```
BaseAgent (ABC)
├── PlannerAgent      — decomposes query into plan
├── ResearchAgent     — gathers information
├── AnalystAgent      — synthesises research
└── ReviewerAgent     — reviews and refines (confidence=0.90)
```

#### Template method pattern

```
BaseAgent.run(inp: AgentInput) → AgentOutput
    ├── start timer
    ├── try:
    │   └── result = self.execute(inp)   ← subclass implements this
    ├── except:
    │   └── log error, set confidence=0.0
    ├── stop timer
    └── return AgentOutput(...)
```

`run()` is the **template method** — it handles cross-cutting concerns
(timing, error handling, structured output). Subclasses only implement
`execute()` with their domain logic.

#### I/O models

```
AgentInput                          AgentOutput
├── query: str                      ├── agent: str
├── context: dict                   ├── output: str
├── previous_output: str            ├── confidence_score: float
├── model: str                      ├── tokens_used: int
└── max_tokens: int                 ├── model_used: str
                                    └── latency_ms: int
```

#### External service connector

`ExternalServiceConnector` provides async helpers for agents that need to
call external systems:

| Method | Purpose |
|--------|---------|
| `fetch_json(endpoint, params)` | HTTP GET via `httpx` |
| `post_json(endpoint, payload)` | HTTP POST via `httpx` |
| `query_database(query, params)` | DB query (placeholder) |

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **Abstract base class** | Enforces contract; `run()` wrapper adds consistent behavior | Protocol/duck typing — less enforcement, no shared logic |
| **Template method pattern** | Separates infrastructure (timing, logging) from business logic | Decorator-based — equivalent but less discoverable |
| **`AgentInput`/`AgentOutput` models** | Validated contracts between orchestrator and agents | Raw dicts — no validation, no IDE support |
| **`httpx` for external calls** | Async-native, modern, well-maintained | `aiohttp` — equivalent; `requests` — sync only |
| **`get_default_agents()` registry** | Single function returns the standard pipeline | Auto-discovery via `__subclasses__()` — fragile, import-order dependent |
| **Placeholder `execute()` bodies** | Agents runnable immediately, output visible in traces | `raise NotImplementedError` — pipeline would crash |

#### Alternatives considered

1. **CrewAI Agent class:** Provides built-in tool calling and memory.
   Planned as a drop-in replacement for `execute()` internals.
   Current `BaseAgent` is designed to be compatible.

2. **LangChain `BaseTool` / `AgentExecutor`:** Full ecosystem for tool
   use. Rejected — too opinionated, locks into LangChain patterns.

3. **One file per agent:** ARCHITECTURE.md specifies `planner.py`,
   `researcher.py`, etc. Current implementation consolidates in
   `integration.py` for bootstrapping. Individual files will be created
   when agents get real LLM logic.

---

### 2.7 Orchestration — `orchestration/planner.py`

**Location:** `backend/orchestration/planner.py`
**Lines of code:** ~190
**Principle enforced:** *Define agent execution order, pass structured state*
(ARCHITECTURE.md §3.3)

#### Pipeline definition

```python
PIPELINE = [
    ("PlannerAgent",   AgentStage.PLANNER),
    ("ResearchAgent",  AgentStage.RESEARCHER),
    ("AnalystAgent",   AgentStage.ANALYST),
    ("ReviewerAgent",  AgentStage.REVIEWER),
]
```

The pipeline is a simple list of tuples — adding, removing, or
reordering stages requires changing one line.

#### WorkflowState

A Pydantic model that accumulates results as it flows through stages:

```
WorkflowState
├── query            (original user input)
├── context          (optional context dict)
├── plan             (← PlannerAgent output)
├── research         (← ResearchAgent output)
├── analysis         (← AnalystAgent output)
├── review           (← ReviewerAgent output)
├── agent_traces     (list of AgentTrace)
├── total_tokens     (running sum)
└── total_cost_usd   (running sum)
```

#### Execution flow (per stage)

```
1. Route      → AgentRouter.route(ctx) → selects model + estimates cost
2. Build      → AgentInput from accumulated state
3. Execute    → agent.run(input) → AgentOutput
4. Record     → Convert to AgentTrace, append to state
5. Enrich     → setattr(state, field, output)
6. Monitor    → monitor.record_agent_call(trace)
```

#### Cost guardrail

After each stage, the orchestrator checks:

```python
if state.total_cost_usd >= settings.max_cost_per_request:
    break  # stop pipeline early
```

This implements ARCHITECTURE.md §8: *Fail fast if cost threshold exceeded*.

#### Factory function

```python
def create_orchestrator(settings, agents=None) -> WorkflowOrchestrator:
    router  = AgentRouter(settings)
    monitor = AgentMonitor(settings)
    return WorkflowOrchestrator(settings, router, monitor, agents)
```

#### Justification

| Choice | Why | Alternative |
|--------|-----|-------------|
| **Sequential pipeline** | Simple, debuggable, deterministic execution order | DAG-based (LangGraph) — more flexible but harder to trace |
| **Shared mutable state** | Each agent sees all prior outputs; natural for enrichment | Message passing — cleaner but adds serialization overhead |
| **Cost guardrail mid-pipeline** | Prevents runaway cost from completing all stages | Pre-flight estimate only — can still overshoot |
| **`register_agent()` method** | Hot-swap agents at runtime; easy testing with mocks | Constructor-only injection — less flexible |
| **Factory function** | Encapsulates wiring; routes, monitor, agents in one call | Manual construction — error-prone, repeated boilerplate |

#### Alternatives considered

1. **LangGraph `StateGraph`:** Compile-time DAG of agent nodes with
   conditional edges. Better for branching workflows (e.g., retry loops,
   parallel agents). The current `WorkflowOrchestrator.execute()` method
   body can be replaced with:
   ```python
   graph = StateGraph(WorkflowState)
   graph.add_node("planner", planner_agent)
   graph.add_edge("planner", "researcher")
   ...
   app = graph.compile()
   result = await app.ainvoke(state)
   ```

2. **CrewAI `Crew`:** Declarative multi-agent orchestration with built-in
   task delegation. Replace the for-loop with:
   ```python
   crew = Crew(agents=[...], tasks=[...], process=Process.sequential)
   result = crew.kickoff()
   ```

3. **AutoGen `GroupChat`:** Autonomous multi-agent conversation. Better
   for open-ended reasoning where agents debate. Replace with:
   ```python
   chat = GroupChat(agents=[...], messages=[])
   result = await chat.arun()
   ```

4. **Async parallel execution:** Run independent agents concurrently via
   `asyncio.gather()`. Viable for ResearchAgent + AnalystAgent if they
   don't depend on each other. Not implemented because the current pipeline
   is strictly sequential per ARCHITECTURE.md §3.3.

---

## 3. Design Patterns & Justifications

| Pattern | Where used | Purpose |
|---------|-----------|---------|
| **Factory** | `create_app()`, `create_orchestrator()` | Testable construction, encapsulated wiring |
| **Lazy Singleton** | `get_settings()`, `get_orchestrator()` | Single instance, deferred initialization |
| **Dependency Injection** | FastAPI `Depends()` in routes | Loose coupling, mockable in tests |
| **Template Method** | `BaseAgent.run()` → `execute()` | Shared infra logic, subclass-specific business logic |
| **Strategy** | `AgentRouter._select_model()` | Swappable routing rules without changing callers |
| **Pipeline** | `PIPELINE` list in planner | Ordered, sequential stage execution |
| **State Object** | `WorkflowState` | Accumulated context flowing through stages |
| **Observer** | `AgentMonitor.record_agent_call()` | Decoupled metrics collection |
| **DTO** | All Pydantic schemas | Type-safe data transfer between layers |
| **Facade** | Route handlers | Hide orchestration complexity from HTTP layer |

---

## 4. Dependency Graph

```
main.py
  └── api/routes.py
        ├── config/settings.py
        ├── api/schemas.py
        ├── orchestration/planner.py
        │     ├── agents/integration.py
        │     │     └── config/settings.py
        │     ├── routing/router.py
        │     │     └── config/settings.py
        │     ├── observability/monitor.py
        │     │     ├── api/schemas.py
        │     │     └── config/settings.py
        │     └── api/schemas.py
        └── observability/monitor.py
```

**Import rules enforced:**

- `main.py` only imports `routes` and `settings`.
- `routes.py` only imports schemas, settings, and orchestration factory.
- `planner.py` imports agents, router, monitor — but NOT routes.
- `router.py` and `monitor.py` only import settings and schemas.
- `integration.py` only imports settings.
- **No circular dependencies.**

---

## 5. Request Lifecycle (Detailed)

```
Client
  │
  ▼
POST /api/run  { "query": "..." }
  │
  ▼
routes.py :: run_pipeline()
  │  ├── Log: pipeline_start (query_length, has_context)
  │  ├── Inject: OrchestratorDep (lazy singleton)
  │  └── Delegate: orchestrator.execute(body)
  │
  ▼
planner.py :: WorkflowOrchestrator.execute()
  │  ├── Create WorkflowState(query, context)
  │  ├── Log: workflow_start
  │  │
  │  ├── Stage 1: _run_stage("PlannerAgent", PLANNER)
  │  │     ├── router.route() → gpt-4o-mini (complexity=MEDIUM)
  │  │     ├── PlannerAgent.run(input) → AgentOutput
  │  │     ├── state.plan = output
  │  │     └── monitor.record_agent_call(trace)
  │  │
  │  ├── ⚡ Cost check: total_cost < max_cost_per_request?
  │  │
  │  ├── Stage 2: _run_stage("ResearchAgent", RESEARCHER)
  │  │     ├── router.route() → gpt-4o-mini
  │  │     ├── ResearchAgent.run(input) → AgentOutput
  │  │     ├── state.research = output
  │  │     └── monitor.record_agent_call(trace)
  │  │
  │  ├── ⚡ Cost check
  │  │
  │  ├── Stage 3: _run_stage("AnalystAgent", ANALYST)
  │  │     ├── router.route() → gpt-4o-mini
  │  │     ├── AnalystAgent.run(input) → AgentOutput
  │  │     ├── state.analysis = output
  │  │     └── monitor.record_agent_call(trace)
  │  │
  │  ├── ⚡ Cost check
  │  │
  │  ├── Stage 4: _run_stage("ReviewerAgent", REVIEWER)
  │  │     ├── router.route() → gpt-4o ★ (high-reasoning for reviewer)
  │  │     ├── ReviewerAgent.run(input) → AgentOutput
  │  │     ├── state.review = output
  │  │     └── monitor.record_agent_call(trace)
  │  │
  │  ├── monitor.evaluate_output(query, review)
  │  ├── monitor.record_request_complete(cost)
  │  ├── Log: workflow_complete
  │  └── Return: RunResponse
  │
  ▼
routes.py :: run_pipeline()
  │  ├── Log: pipeline_end (tokens, cost, score)
  │  └── Return: HTTP 200 + JSON
  │
  ▼
Client receives:
{
  "result": "...",
  "evaluation_score": 0.7,
  "agent_trace": [ ... 4 entries ... ],
  "metrics": { "total_tokens": 0, "total_cost_usd": 0.048, "total_latency_ms": 2 }
}
```

---

## 6. Cost Control Implementation

ARCHITECTURE.md §8 defines five cost control requirements. Here is how
each is implemented:

| Requirement | Implementation | Location |
|-------------|---------------|----------|
| Hard cap per request | `if total_cost >= max_cost_per_request: break` | `planner.py:execute()` |
| Token limit per agent | `max_tokens` from settings passed to every agent | `router.py:route()` |
| No infinite loops | Pipeline is a finite list; no retry loops | `planner.py:PIPELINE` |
| Logging for every LLM call | `monitor.record_agent_call()` after each stage | `planner.py:_run_stage()` |
| Fail fast on threshold | Pipeline `break` stops further stages | `planner.py:execute()` |

**Budget example (4 stages, MEDIUM complexity):**

| Stage | Model | Est. cost |
|-------|-------|-----------|
| Planner | gpt-4o-mini | $0.002 |
| Researcher | gpt-4o-mini | $0.002 |
| Analyst | gpt-4o-mini | $0.002 |
| Reviewer | gpt-4o | $0.042 |
| **Total** | | **~$0.048** |

Well under the $0.50 default cap and the $20 development budget target.

---

## 7. Alternatives Considered

### 7.1 Framework choices

| Area | Chosen | Alternatives considered | Why chosen |
|------|--------|------------------------|------------|
| **Web framework** | FastAPI | Flask, Django REST, Litestar | Async-native, auto-docs, Pydantic integration |
| **Config management** | pydantic-settings | python-decouple, dynaconf | Same Pydantic ecosystem, type-safe validation |
| **HTTP client** | httpx | aiohttp, requests | Async + sync API, modern, well-maintained |
| **Logging** | stdlib `logging` + JSON formatter | structlog, loguru | Zero dependencies; structlog is a good upgrade path |
| **Validation** | Pydantic v2 | attrs, marshmallow, dataclasses | FastAPI native, fastest Pydantic version |

### 7.2 Orchestration approaches

| Approach | Pros | Cons | Status |
|----------|------|------|--------|
| **Custom sequential (current)** | Simple, debuggable, no dependencies | No branching, no parallelism | Implemented |
| **LangGraph** | DAG with conditional edges, state management | LangChain dependency, learning curve | Planned |
| **CrewAI** | Declarative, role-based agents, built-in delegation | Opinionated, less control over routing | Compatible |
| **AutoGen** | Multi-agent conversation, autonomous reasoning | Hard to control cost, non-deterministic | Future research |
| **Temporal / Prefect** | Durable workflows, retry policies, observability | Infrastructure overhead, overkill for 4 agents | Not planned |

### 7.3 Observability backends

| Backend | Pros | Cons | Status |
|---------|------|------|--------|
| **In-memory (current)** | Zero infra, instant reads | Lost on restart, single process | Implemented |
| **LangFuse** | Open-source, LLM-specific tracing | Self-hosted or SaaS cost | Placeholder ready |
| **LangSmith** | Tight LangChain integration | Vendor lock-in | Placeholder ready |
| **OpenTelemetry** | Industry standard, vendor-neutral | Complex setup, generic (not LLM-aware) | Planned |
| **Prometheus + Grafana** | Battle-tested, alerting | Requires infrastructure | Future |

### 7.4 Evaluation strategies

| Strategy | Accuracy | Cost | Latency | Status |
|----------|----------|------|---------|--------|
| **Heuristic (current)** | Low | Zero | ~0ms | Implemented |
| **LLM-as-judge** | High | $0.01–0.05/eval | 500–2000ms | Recommended next |
| **RAGAS** | High (RAG-specific) | Moderate | Moderate | If RAG is added |
| **Trained classifier** | Medium-High | Near zero | ~5ms | If labelled data exists |
| **Human review** | Highest | High | Hours | For calibration only |

---

## 8. Current Limitations & Next Steps

### Limitations

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|----------------|
| 1 | Agents return placeholder text | No real LLM calls | Wire `execute()` to OpenAI/Anthropic SDK |
| 2 | In-memory metrics lost on restart | No historical data | Add Redis or PostgreSQL persistence |
| 3 | Sequential-only pipeline | No parallel agents | Introduce `asyncio.gather()` or LangGraph |
| 4 | Single-process only | No horizontal scaling | Add Redis for shared state + Gunicorn workers |
| 5 | No authentication | Open API | Add JWT/OAuth middleware |
| 6 | Heuristic evaluation only | Low accuracy scoring | Implement LLM-as-judge |
| 7 | Static cost rates | May drift from actual pricing | Fetch from provider APIs or config |

### Recommended next steps (priority order)

1. **Wire real LLM calls** — Replace `execute()` stubs with OpenAI SDK calls.
2. **Add prompt templates** — Load from `prompts/master_prompt.txt` per agent.
3. **Implement evaluation** — LLM-as-judge for production quality scoring.
4. **Add unit tests** — pytest + httpx `AsyncClient` for every endpoint.
5. **Connect LangFuse** — Production observability with minimal effort.
6. **Frontend client** — React dashboard showing traces and metrics.
7. **CI/CD pipeline** — Lint, test, build, deploy per ARCHITECTURE.md §7.

---

*This document describes the architecture as implemented. It should be
updated as the system evolves.*
