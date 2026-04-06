# Agent OS Architecture

> Last updated: 2026-04-02
> Research references: RESEARCH-orchestration-core.md, RESEARCH-framework-comparison.md, RESEARCH-memory-subsystem-design.md, RESEARCH-agent-memory.md

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

---

## Orchestrator Architecture

The orchestrator is built as a graph state machine with 6 core modules:

```
engine.py          → Orchestration entry point
├── graph/         → Graph state machine (state, nodes, conditional edges)
├── context/       → ContextCompiler + ContextManager (write/select/compress/isolate)
├── memory/        → MemoryService (working/session/episodic/semantic tiers)
├── tools/         → ToolExecutor + Registry + Guardrail
├── communication/ → CommunicationBus (inter-agent messaging)
└── concurrency/   → ConcurrencyController (parallel execution limits)
```

### Design Decisions (Confirmed)

1. **Graph State Machine** — Built at LangGraph level (not pydantic-ai which is too high, not LangChain which is too low)
2. **Context Engineering** — ContextCompiler = "assemble what"; ContextManager = "manage lifecycle" (write/select/compress/isolate)
3. **Memory Tiers** — Four-layer model (Working → Session → Episodic → Semantic) with trust-domain isolation
4. **Async Tool Loop** — ToolExecutor with guardrail → execute → feedback closed loop
5. **Agent Communication** — CommunicationBus with trust-domain isolation (Workspace/Session/Agent/Global)
6. **Concurrency** — Semaphore-based with configurable limits per agent/tool

---

## Memory Subsystem Architecture

### Four-Layer Memory Model

```
┌─────────────────────────────────────────────────────────┐
│ L0: Working Memory (Context Window)                      │
│   始终可见，直接可用，受限于 LLM context window           │
│   管理: ContextCompiler 编译，Compaction 压缩            │
├─────────────────────────────────────────────────────────┤
│ L1: Session Memory (会话记忆)                            │
│   当前会话完整历史 + 状态，会话结束即归档                  │
│   存储: SQLite，访问: recall() / get_recent()            │
├─────────────────────────────────────────────────────────┤
│ L2: Episodic Memory (情景记忆)                           │
│   跨会话经验片段，带时间衰减，定期提炼到 Semantic          │
│   存储: 向量DB，访问: 语义检索 + 时序索引                 │
├─────────────────────────────────────────────────────────┤
│ L3: Semantic Memory (语义记忆)                           │
│   持久化知识、实体关系、用户画像（Graphiti + Neo4j）      │
│   存储: KG + 向量DB，访问: 图查询 + 混合检索             │
└─────────────────────────────────────────────────────────┘
```

### Layer Migration Rules

| Migration | Trigger | Method | Guarantee |
|-----------|---------|--------|-----------|
| L0 → L1 | End of each inference turn | Synchronous batch write (mark importance > threshold) | Zero data loss |
| L1 → L2 | Session end (explicit / timeout) | Async (buffer first → background compress) | Checkpoint-based recovery |
| L2 → L3 | Periodic maintenance (Heartbeat/Cron) | Async full scan (cross-episodic analysis) | Non-blocking |
| L3 → L2 | Knowledge outdated | Demote to episodic | — |
| L2 → ∅ | Time decay + low importance + no access | Active forgetting | Soft delete by default |

### Memory Service API

```python
# Write
await memory.store(content, scope, metadata, memory_type)  → MemoryRef
await memory.append(block_label, content, scope)            → None
await memory.update(memory_id, content, merge_strategy)     → MemoryRef

# Read
await memory.recall(query, top_k, scope, strategy)          → list[MemoryItem]
await memory.search(query, filters, scope)                  → list[MemoryItem]

# Management
await memory.compress(target, strategy)                     → MemoryRef
await memory.forget(memory_id, reason, mode)                → None
await memory.reflect(trigger)                                → list[MemoryRef]
```

### Compression Triggers

| Saturation | Action | Blocking |
|------------|--------|----------|
| > 70% | Async compress (Observer evaluates → Reflector summarizes) | No |
| > 85% | Sync compress (block, but 2s timeout → skip to next turn) | Yes |
| Daily | Episodic → Semantic extraction via Heartbeat/Cron | No |
| Weekly | Semantic dedup + low-score cleanup | No |

### Importance Scoring

Five dimensions with configurable weight templates per Agent type:

```python
WEIGHT_TEMPLATES = {
    "coding":    {"relevance": 0.20, "recency": 0.20, "uniqueness": 0.20, "confidence": 0.30, "frequency": 0.10},
    "research":  {"relevance": 0.25, "recency": 0.30, "uniqueness": 0.20, "confidence": 0.15, "frequency": 0.10},
    "assistant": {"relevance": 0.40, "recency": 0.20, "uniqueness": 0.15, "confidence": 0.15, "frequency": 0.10},
}
```

Three-phase scoring: initial (on store) → incremental (on access) → full rescore (daily, exponential decay)

### Trust Domain Isolation

```
Global     — Cross-project universal knowledge (KG), all Agents read-only
Workspace  — Same-project Agents share Semantic Memory, cross-domain read with auth
Session    — Same-task Agents share Working/Session Memory, destroyed on task end
Agent      — Per-Agent isolated Working Memory, communication only via CommunicationBus
```

### Tool Result Lifecycle

- New tool results enter "safety window" (3-5 inference turns, cannot be forgotten)
- After safety window: participate in importance scoring, below threshold → forget
- **Key rule**: `result` (temporary) can be discarded; `reasoning_context` (why called, what decision) is permanent
- Reference: ReasoningBank — "failure experience is more valuable than success"

### Module Boundary: MemoryService vs ContextCompiler

```
MemoryService ("knows what"):
├── recall() / search() → return memory items
├── store() / update() → write memories
├── compress() / forget() → lifecycle management
└── reflect() → trigger memory consolidation

ContextCompiler ("assembles what"):
├── compile(task, agent_state) → output minimal usable context
│   ├── 1. Call memory.recall() to get relevant memories
│   ├── 2. Apply Include/Exclude/Summarize strategy
│   ├── 3. Decide context depth based on task type
│   └── 4. Output compiled context
└── Does NOT directly operate on storage, only consumes MemoryService output
```

### Memory Version Control

- Every update preserves the old version (recent N versions per memory)
- Implemented at MemoryService layer (cross-storage unified)
- Supports `rollback(memory_id, version=N)`
- Expired versions auto-cleaned

### Storage Evolution

| Stage | Storage | Vector Index | KG | Duration |
|-------|---------|-------------|-----|----------|
| MVP | SQLite + sqlite-vec | FAISS/Hnswlib (in-memory) | None | 2-3 weeks |
| V1 | SQLite + sqlite-vec | sqlite-vec | + Graphiti + Neo4j (KG validation) | 4-6 weeks |
| V2 | PostgreSQL + pgvector | pgvector | Graphiti + Neo4j (production) | 6-8 weeks |

### Observer/Reflector Implementation

- **Location**: Internal sub-agents within MemoryService
- **Observer**: Evaluates current context, decides what's worth compressing
- **Reflector**: Executes compression, generates consolidated summaries
- **MVP**: Uses same model as main orchestrator (simplify)
- **V1**: Switches to dedicated small model (e.g., gpt-4o-mini level, save quota)

---

## Frontend Architecture

```
React Flow (visual layer)  ←→  Zustand (state layer)  ←→  API Client (data layer)
     ↓                            ↓                          ↓
  Agent Node                 flowStore                   GET /api/agents
  Tool Node                  agentStore                  POST /api/execute
  Prompt Node                uiStore                     SSE /api/events
  Data Edge
```

### Memory Visualization

- **Agent Node Panel (lightweight)**: Working Memory size + count, recent 3 memory summaries, "more" → jump to Memory Panel
- **Independent Memory Panel (full)**: All 4 layers visible, each memory shows content + score + timestamp + source, compress/forget event history
- **Production**: No event stream (too noisy)
- **Debug Mode**: Toggle to show all state change events (compress/forget/migrate)

---

## Frontend-Backend Communication

| Protocol | Use Case |
|----------|----------|
| **HTTP REST** | CRUD operations (agents, prompts, resources) |
| **SSE** | Agent execution status streaming, real-time logs |
| **WebSocket** | Interactive sessions, bidirectional communication |
| **gRPC** | Inter-service communication (backend microservices) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React + Next.js 15 + React Flow + Zustand + TailwindCSS |
| BFF | Next.js API Routes |
| API Gateway | FastAPI (Python) |
| Orchestrator | Python + Graph State Machine |
| gRPC | protobuf + grpcio |
| Database | SQLite (MVP) → PostgreSQL (production) |
| Vector DB | FAISS (MVP) → sqlite-vec (V1) → pgvector (V2) |
| KG | Graphiti + Neo4j (V1+) |
| Embeddings | text-embedding-3-small / local models |

---

## 设计决策记录

> 以下决策基于项目专家-00 与 Main Agent 三轮设计讨论（2026-04-02）确认

共 12 个议题。

### D-01: 编排层自研定位 ✅
- **决策**: 在 LangGraph 层级自研图状态机，不用 pydantic-ai（太高）也不用 LangChain（太低）
- **依据**: 7 框架对比研究（LangGraph/LangChain/pydantic-ai/Google ADK/OpenAI SDK/CrewAI/AutoGen）
- **参考**: `RESEARCH-framework-comparison.md`

### D-02: 编排层五大自研模块 ✅
- **决策**: ContextCompiler, MemoryService, ToolExecutor, CommunicationBus, ConcurrencyController
- **参考**: `RESEARCH-orchestration-core.md`

### D-03: 记忆四层模型 ✅
```
Working Memory  ← Context Window，当前推理上下文
Session Memory  ← 会话级持久化，对话历史 + 状态
Episodic Memory  ← 情景记忆，按时间/事件索引
Semantic Memory  ← 语义知识库，向量 + KG 双索引
```

### D-04: 双触发压缩机制 ✅
- **70% context 饱和度**: 异步压缩（不阻塞推理）
- **85% context 饱和度**: 同步压缩（阻塞，但上限 2 秒超时）
- **主动评分**: 每次 store() 后评分，低价值标记待压缩
- **周期性**: 每日 Episodic→Semantic，每周 Semantic 去重

### D-05: 信任域隔离 ✅
```
Workspace 级 — 同项目 Agent 共享 Semantic Memory
Session 级 — 同次任务 Agent 共享 Working/Session Memory
Agent 级 — 每个 Agent 独立 Working Memory
Global — 跨项目通用知识（KG）
```
- 共享规则: Semantic 可授权跨域读取，Working 完全隔离
- 通信仅通过 CommunicationBus，不直接读取对方 Working Memory

### D-06: ContextCompiler vs MemoryService 边界 ✅
- **MemoryService**: 负责"知道什么"（recall/search/store/compress/forget/reflect）
- **ContextCompiler**: 负责"组装什么"（compile → 调用 MemoryService + 策略选择 → 输出最小上下文）
- `get_context()` 放在 ContextCompiler，MemoryService 暴露 `recall()` 和 `search()`

### D-07: 层间迁移规则 ✅
| 迁移 | 方式 | 时机 | 保证 |
|------|------|------|------|
| Working → Session | 同步批量 | 每轮推理结束 | 不丢数据 |
| Session → Episodic | 异步 | 会话结束（先写 buffer） | checkpoint 恢复 |
| Episodic → Semantic | 异步定时 | Heartbeat/Cron | 不抢推理资源 |

### D-08: 重要性评分 ✅
- **五维度**: relevance(0.30) + recency(0.25) + uniqueness(0.20) + confidence(0.15) + frequency(0.10)
- **权重可配置模板**:
  - coding: confidence 优先 (0.30)
  - research: recency 优先 (0.30)
  - assistant: relevance 优先 (0.40)
- **评分策略**: 写入时初评 → 访问时增量 → 定时全量重评
指数衰减）

### D-09: 工具结果生命周期 ✅
- **方案**: C + 安全期（3-5 轮推理不可遗忘）
- **推理链绑定**: result(可丢) + reasoning_context(永久保留)
- **参考**: ReasoningBank "失败经验比成功经验更有价值"

### D-10: 前端记忆可视化 ✅
- **Agent 节点面板（轻量）**: Working Memory 大小 + 最近 3 条摘要 + "更多"跳转
- **独立 Memory 面板（完整）**: 四层全可见 + score + 来源 + 压缩/遗忘历史
- **调试模式**: 开关显示所有状态变化事件流

### D-11: KG 层选型 ✅ (2026-04-06 更新)
- **决策**: 轻量 SQLite KG（万级节点以下）→ 预留 Neo4j 迁移路径
- **核心价值**: 时序事实追踪（valid_from/valid_to），事实变化有历史版本
- **实现**: SQLite 两张表（entities + relations），properties 用 JSON 扩展
- **查询**: JOIN + 递归 CTE 实现图遍历，支持 N 跳
- **迁移路径**: 接口抽象化，未来可无缝切换 Neo4j

### D-12: 存储介质演进 ✅ (2026-04-06 更新)
| 阶段 | 存储 | 向量 | KG | 状态 |
|------|------|------|-----|------|
| MVP ✅ | InMemoryStore + FAISS 内存 | FAISS (faiss-cpu) | 无 | 已完成 |
| V1 (当前) | SQLiteStore + FAISS 文件持久化 | FAISS (read/write_index) | SQLite KG (entities + relations) | 实施中 |
| V2 | PostgreSQL + pgvector | pgvector | Neo4j（可选，10万+节点时） | 远期 |

### 待讨论
- 记忆版本控制（每条记忆保留 N 个历史版本，支持 rollback）
- 记忆安全与权限（读/写/删分级控制，跨信任域访问策略）
- 多模态记忆（文本/代码/图片/对话混合存储）
- RL 优化引入时机（V2+，需足够数据积累）

