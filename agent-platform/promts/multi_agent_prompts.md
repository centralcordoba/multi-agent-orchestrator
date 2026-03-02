# Multi-Agent Orchestrator — Prompts para Claude

Este archivo contiene todos los prompts para generar la estructura modular de tu proyecto multi-agent en Python usando FastAPI y orquestación AI. Incluye Fases 1 a 5, listo para usar en Claude.

---

## Fase 1 — Backend Base (FastAPI)

**Prompt:**
```
Using the ARCHITECTURE.md provided,
generate a production-ready FastAPI bootstrap structure.

Requirements:
- backend/main.py
- backend/api/routes.py
- Pydantic request/response schemas
- No agent logic
- Modular design
- No business logic in main.py
- Clean dependency injection structure
- Professional logging setup

Return only the code for:
1) main.py
2) routes.py
3) basic schemas
```

---

## Fase 2 — Routing Layer (Agent Routing)

**Prompt:**
```
Using the ARCHITECTURE.md, generate a professional Python module for the routing layer of a multi-agent platform.

Requirements:
- File: backend/routing/router.py
- Functionality: route incoming tasks to the appropriate AI agent based on task type
- Use dependency injection style
- Keep backend/api/routes.py clean; only orchestrate the agent calls
- Include type hints and Pydantic models for input/output
- Include comments and docstrings for clarity
- No agent logic itself, just routing placeholders
- Return only the Python code for router.py
```

---

## Fase 3 — Planner / Agent Orchestrator

**Prompt:**
```
Using the ARCHITECTURE.md and routing layer, generate a Python module for the agent orchestrator.

Requirements:
- File: backend/orchestration/planner.py
- Functionality: coordinate multiple agents to accomplish a task
- Receive a task and produce a sequence of agent calls
- Include placeholders for CrewAI / LangGraph / AutoGen integrations
- Use async/await style for agents
- Include logging and error handling
- Type hints and Pydantic models
- Modular design so new agents can be added easily
- Return only the Python code for planner.py
```

---

## Fase 4 — Observability / Evaluation Layer

**Prompt:**
```
Based on ARCHITECTURE.md and previous layers, generate a Python module for agent observability.

Requirements:
- File: backend/observability/monitor.py
- Functionality: log agent requests, responses, performance metrics
- Integrate placeholders for LangFuse / LangSmith
- Include structured logging and async support
- Include functions to evaluate agent outputs against expected results
- Modular, reusable, production-ready style
- Return only the Python code for monitor.py
```

---

## Fase 5 — Multi-Agent Integration / Reusable Templates

**Prompt:**
```
Based on ARCHITECTURE.md, generate a Python module for multi-agent integration and reusable templates.

Requirements:
- File: backend/agents/integration.py
- Functionality: provide reusable agent templates and helpers
- Include examples for calling external systems (databases, APIs)
- Include async examples and type hints
- Include error handling and logging
- Design so new agent types can be added quickly
- Return only the Python code for integration.py
```

