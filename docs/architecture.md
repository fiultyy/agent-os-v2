# Architecture

## Runtime Architecture

```
User → Next.js BFF (SSR) → API Gateway (HTTP/SSE/WS) → Python Microservices (gRPC)

Browser: React Flow visual layer + Zustand state layer
Backend: Agent Orchestrator + supporting microservices
```

## Service Map

| Service | Port | Description |
|---------|------|-------------|
| **web** | 3000 | Next.js frontend with React Flow canvas |
| **gateway** | 8000 | API Gateway (FastAPI, HTTP/SSE/WS) |
| **orchestrator** | 8001 | Agent orchestration engine (core) |
| **prompt-manager** | 8002 | Prompt template & version management |
| **conversation-observer** | 8003 | Conversation monitoring & analytics |
| **resource-manager** | 8004 | Provider adapters & model routing |

## Orchestrator Architecture

The orchestrator is built as a graph state machine with 6 core modules:

```
engine.py          → Orchestration entry point
├── graph/         → Graph state machine (state, nodes, conditional edges)
├── context/       → ContextCompiler + ContextManager (write/select/compress/isolate)
├── memory/        → MemoryService (working/episodic/semantic/core tiers)
├── tools/         → ToolExecutor + Registry + Guardrail
├── communication/ → CommunicationBus (inter-agent messaging)
└── concurrency/   → ConcurrencyController (parallel execution limits)
```

### Design Decisions

1. **Graph State Machine** — Built at LangGraph level (not pydantic-ai which is too high, not LangChain which is too low)
2. **Context Engineering** — ContextCompiler assembles LLM context; ContextManager handles lifecycle
3. **Memory Tiers** — Working (current), Episodic (past), Semantic (KG), Core (identity)
4. **Async Tool Loop** — ToolExecutor with guardrail → execute → feedback closed loop
5. **Agent Communication** — Bus pattern with session/agent/global scopes
6. **Concurrency** — Semaphore-based with configurable limits per agent/tool

## Frontend Architecture

```
React Flow (visual layer)  ←→  Zustand (state layer)  ←→  API Client (data layer)
     ↓                            ↓                          ↓
  Custom Nodes               flowStore                   /api/* → BFF rewrite
  Custom Edges               agentStore
                              uiStore
```

## Data Flow

```
1. User creates flow in canvas → Zustand store
2. User triggers "run" → API call to BFF
3. BFF proxies to Gateway → Gateway routes to Orchestrator
4. Orchestrator runs graph: compile context → call LLM → execute tools → loop
5. Results stream back via SSE → Zustand → React Flow updates
```

## Communication Protocols

- **Frontend ↔ Gateway**: HTTP REST + SSE (streaming) + WebSocket (real-time)
- **Gateway ↔ Services**: gRPC (internal service-to-service)
- **External APIs**: HTTP (provider adapters)
