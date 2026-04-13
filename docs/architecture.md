# Agent OS Architecture

> Last updated: 2026-04-11
> Research references: RESEARCH-orchestration-core.md, RESEARCH-framework-comparison.md, RESEARCH-memory-subsystem-design.md, RESEARCH-agent-memory.md

---

## Meta Agent vs Side Agent 区分

### Meta Agent（Agent 自身机制）

**本质**：一个 Agent 如何**生成、管理、通信**子 Agent

| 组件 | 说明 |
|------|------|
| **Subagent** | 可嵌套，包含深度限制（OpenClaw 最大 10 层） |
| **TeamAgent** | Agent 间通信，如 Claude Code /teams |
| **Sandbox** | 隔离执行环境，独立进程/容器 |
| **Conditional Spawn** | 条件触发型任务生成，用于规模 scale |
| **Parallel Execution** | 基础能力，多 Agent 并行 |

**特点**：
- **层次结构**：父 → 子 → 孙
- **生命周期管理**：父管理子的创建和销毁
- **通信**：父子通信、兄弟通信

### Side Agent（旁路 Agent）

**本质**：**独立运行的 Agent**，观察、影响主 Agent

| 角色 | 说明 |
|------|------|
| **Kairos** | 时机感知，LIF 电位触发 |
| **Verifier** | 错误检测，Turn 边界验证 |
| **Dreamer** | 记忆巩固，Cron 定时 |
| **Critic** | 事实锚定，置信度触发 |

**特点**：
- **独立进程**：不隶属于 Main Agent 的层级
- **平等关系**：不是父子，是同行/观察者
- **影响方式**：通过 CA Layer 注入（慢/中/快通道）

### 核心区别

| 维度 | Meta Agent | Side Agent |
|------|-----------|-----------|
| **关系** | 层级（父子） | 平行（同行） |
| **生命周期** | 父管理子 | 独立运行 |
| **通信方式** | 直接调用/消息 | CA 注入 |
| **目的** | 任务分解执行 | 外部视角审视 |
| **触发** | 主 Agent 显式调用 | 事件/电位/时间触发 |

---

## Meta Agent 条件型任务生成

Meta Agent 支持**条件触发型 spawn**，用于任务规模 scale：

| 类型 | 说明 | 参考 |
|------|------|------|
| **Cron Spawn** | 定时触发新任务 | OpenClaw Cron |
| **Heartbeat Spawn** | 周期检查触发 | OpenClaw Heartbeat |
| **Event Spawn** | 事件触发（如 LIF 电位达标） | Kairos 电位 |
| **Queue Spawn** | 任务队列满时触发 | 消息队列 |

---

## 核心创新：Context Architecture 三路构建

Agent OS 的 CA（Context Architecture）比传统模式多一种**推理式动态影响**：

| CA 构建方式 | 构建时机 | 驱动来源 | 影响力 |
|------------|---------|---------|--------|
| **1. Base Prompt（静态）** | 启动时 | 配置文件、SOUL/AGENTS | 固定、持久 |
| **2. 记忆系统（动态）** | 每轮 Turn | LIF 漏电积分 + 蝴蝶翼 | 动态、上下文相关 |
| **3. 旁路 LLM（推理式）** | Turn 间 | Verifier / Kairos / Dreamer | 推理驱动、适时注入 |

### 核心创新点

**记忆系统 = LIF 漏电积分 + 蝴蝶翼 + 旁路 LLM**

- **LIF（Leakage Integration Fire）**：多轮观察累积到阈值才触发写入，防止噪声污染
- **蝴蝶翼**：正向翼（归纳）↔ 反向翼（锚定）双向联想，记忆不是检索而是**激活-塑造**
- **旁路 LLM**：Kairos（时机注入）+ Verifier（错误检测）+ Dreamer（夜间巩固）

### 参考实现

| 组件 | 参考来源 |
|------|---------|
| Tools / Skills / Provider Router | OpenClaw, Claude Code |
| 编排模式（Flow/DAG/Loop/Cron） | Claude Code (/loop), OpenClaw (Cron), Hermes Agent |
| Base Prompt Layer Stack | OpenClaw SOUL/AGENTS 模式 |
| 记忆系统 LIF + FIRE | MemGPT 三层设计 |
| 蝴蝶翼双向联想 | 认知科学双过程理论 |
| 旁路 LLM | OpenClaw Verifier + Kairos |

---

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
engine.py          → FastAPI app init + lifecycle (184 lines)
├── api/
│   ├── models.py      → Request/Response BaseModel
│   └── routes/
│       ├── agents.py     → Agent CRUD endpoints
│       ├── chat.py       → Chat + Execute SSE
│       ├── memory.py     → Memory endpoints
│       └── entities.py   → KG entity endpoints
├── services/
│   ├── agent_manager.py  → Agent lifecycle
│   ├── llm_client.py    → LLM client wrapper
│   └── _state.py       → Shared state
├── graph/         → Graph state machine (state, nodes, conditional edges)
├── context/       → ContextCompiler + ContextManager (write/select/compress/isolate)
├── memory/        → MemoryService + Recall strategies + Storage
├── tools/         → ToolExecutor + Registry + Guardrail
├── communication/ → CommunicationBus (inter-agent messaging)
└── concurrency/   → ConcurrencyController (parallel execution limits)
```

### Orchestration Modes

Agent OS supports **multiple orchestration patterns**:

| 模式 | 描述 | 适用场景 |
|------|------|---------|
| **Flow** | 有向无环图，按拓扑顺序执行 | 线性任务、流水线 |
| **DAG** | 有向无环图，节点可并行 | 多分支任务、依赖管理 |
| **Loop** | 循环执行直到满足退出条件 | 迭代优化、轮次探索 |
| **Cron** | 定时触发，周期性执行 | 监控、定时任务 |

#### Flow Mode

```python
class FlowOrchestrator:
    """线性流程编排"""
    
    async def run(self, pipeline: Pipeline, initial_input: Any) -> Any:
        state = initial_input
        for node in pipeline.topological_sort():
            state = await node.execute(state)
        return state
```

#### DAG Mode

```python
class DAGOrchestrator:
    """有向无环图编排，支持并行"""
    
    async def run(self, dag: DAG, initial_input: Any) -> Any:
        levels = dag.levelize()  # 同层节点可并行
        state = initial_input
        for level_nodes in levels:
            results = await asyncio.gather(*[
                node.execute(state) for node in level_nodes
            ])
            state = self._merge_state(state, results)
        return state
```

#### Loop Mode

```python
class LoopOrchestrator:
    """循环编排，直到满足退出条件"""
    
    def __init__(self, max_iterations: int = 100, exit_condition: Callable = None):
        self.max_iterations = max_iterations
        self.exit_condition = exit_condition or (lambda s: s.converged)
    
    async def run(self, loop_body: GraphNode, initial_state: Any) -> Any:
        state = initial_state
        for i in range(self.max_iterations):
            state = await loop_body.execute(state)
            if self.exit_condition(state):
                break
        return state
```

#### Cron Mode

```python
class CronOrchestrator:
    """定时编排，周期性触发"""
    
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.jobs: dict[str, CronJob] = {}
    
    def schedule(self, name: str, pattern: str, handler: GraphNode, ttl: int = 86400 * 3):
        self.jobs[name] = CronJob(pattern=pattern, handler=handler, ttl=ttl, last_run=None)
    
    async def tick(self):
        now = datetime.now()
        for name, job in self.jobs.items():
            if job.should_run(now):
                await job.execute()
                job.last_run = now
```

### Meta Agent：动态编排模式选择

参考: Claude Code (/loop), OpenClaw (Cron), Hermes Agent

```python
class MetaOrchestrator:
    """元编排器：根据任务特征动态选择编排模式"""
    
    async def plan(self, task: Task) -> OrchestrationPlan:
        analysis = await self._analyze_task(task)
        
        if analysis.has_time_trigger:
            return OrchestrationPlan(mode="cron", config=analysis.cron_config)
        elif analysis.has_iteration:
            return OrchestrationPlan(mode="loop", config=analysis.loop_config)
        elif analysis.has_parallel_branches:
            return OrchestrationPlan(mode="dag", config=analysis.dag_config)
        else:
            return OrchestrationPlan(mode="flow", config=analysis.flow_config)
```

### Orchestration Mode Selection

| 任务特征 | 推荐模式 |
|---------|---------|
| 线性流水线 | Flow |
| 多分支并行 | DAG |
| 迭代优化 | Loop |
| 定时监控 | Cron |
| 复杂混合 | Meta Agent 动态选择 |

### Design Decisions (Confirmed)

1. **Graph State Machine** — Built at LangGraph level (not pydantic-ai which is too high, not LangChain which is too low)
2. **Context Engineering** — ContextCompiler = "assemble what"; ContextManager = "manage lifecycle" (write/select/compress/isolate)
3. **Memory Tiers** — Four-layer model (Working → Session → Episodic → Semantic) with trust-domain isolation
4. **Async Tool Loop** — ToolExecutor with guardrail → execute → feedback closed loop
5. **Agent Communication** — CommunicationBus with trust-domain isolation (Workspace/Session/Agent/Global)
6. **Concurrency** — Semaphore-based with configurable limits per agent/tool
7. **Multi-Mode Orchestration** — Flow / DAG / Loop / Cron 由 Meta Agent 动态选择

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
await memory.search(query, filters, scope)                  → list[MemoryItem]  # alias: recall(mode=KEYWORD)

# Management
await memory.compress(target, strategy)                     → MemoryRef
await memory.forget(memory_id, reason, mode)                → None
await memory.reflect(agent_id, trigger)                     → list[MemoryRef]   # episodic→semantic consolidation
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
    "research":  {"recency": 0.15, "frequency": 0.10, "relevance": 0.40, "emotional_weight": 0.10, "actionability": 0.25},
    "coding":    {"recency": 0.20, "frequency": 0.20, "relevance": 0.30, "emotional_weight": 0.05, "actionability": 0.25},
    "general":   {"recency": 0.25, "frequency": 0.15, "relevance": 0.25, "emotional_weight": 0.15, "actionability": 0.20},
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

- New tool results enter "safety window" with a turn counter (default: 4 sweeps, cannot be forgotten)
- Each `ActiveForgetting.run_sweep()` decrements `safety_turns_remaining`; at 0 the `safety_deadline` flag is cleared
- After safety window: participate in importance scoring, below threshold → forget
- **Key rule**: `result` (temporary) can be discarded; `reasoning_context` (why called, what decision) is permanent
- Reference: ReasoningBank — "failure experience is more valuable than success"

### Module Boundary: MemoryService vs ContextCompiler

```
MemoryService ("knows what"):
├── _crud.py          → store/get/update/delete operations
├── _blocks.py        → Core Memory Block operations
├── _session.py       → Session lifecycle (create/archive/destroy)
├── _recall/          → Recall strategy implementations
│   ├── base.py           → RecallStrategy ABC
│   ├── keyword_recall.py → Keyword match
│   ├── semantic_recall.py → Vector embedding
│   ├── kg_recall.py     → Knowledge Graph
│   └── shared_recall.py → Shared scope
└── service.py        → Facade (346 lines, orchestrates sub-components)

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
- **五维度**: recency(0.25) + frequency(0.15) + relevance(0.25) + emotional_weight(0.15) + actionability(0.20)
- **权重可配置模板**:
  - RESEARCH: relevance 优先 (0.40), actionability (0.25), recency (0.15)
  - CODING: relevance (0.30), recency (0.20), frequency (0.20), actionability (0.25)
  - GENERAL: 均衡分布 (recency 0.25, relevance 0.25, actionability 0.20, emotional_weight 0.15, frequency 0.15)
- **评分策略**: 写入时初评 → 访问时增量 → 定时全量重评（指数衰减）

### D-09: 工具结果生命周期 ✅
- **方案**: 安全期轮次计数器（默认 4 轮 sweep 后解除保护，进入正常重要性评分）
- **实现**: `ActiveForgetting.run_sweep()` 每次递减 `safety_turns_remaining`，到 0 时移除 `safety_deadline` 标记
- **推理链绑定**: result(可丢) + reasoning_context(永久保留)
- **参考**: ReasoningBank "失败经验比成功经验更有价值"

### D-10: 前端记忆可视化 ✅
- **独立 Memory 面板（完整）**: 四层全可见 + score + 来源 + 压缩/遗忘历史
- **调试模式**: 开关显示所有状态变化事件流
- **V2 计划**:
  - Agent 节点面板（轻量）: Working Memory 大小 + 最近 3 条摘要 + "更多"跳转
  - 事件历史时间线: 可视化压缩/遗忘/迁移事件流

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

---

## Layer 3: Tool Execution 分层抽象

> 状态: **设计阶段** | 实现状态: 待启动
> 更新: 2026-04-11

### 设计目标

| 目标 | 说明 |
|------|------|
| 接口隔离 | 每个模块有抽象基类，具体实现在 impl/ |
| 分层解耦 | L3.1 → L3.2 → L3.3 → L3.4 单向依赖 |
| Skill 可复用 | Browser/Code/Memory 等 Skill 封装为独立包 |
| 可插拔 | ToolScope/ToolCategory 可独立替换 |

### 四层结构

```
Layer 3: Tool Execution
├── L3.1: Tool Kernel（工具内核）
│   ├── executor.py      # 执行器：调度、并发、超时
│   └── result.py        # 统一结果格式
│
├── L3.2: Tool Interface（工具接口）
│   ├── registry.py      # 抽象注册表（Protocol）
│   ├── guardrail.py     # 安全策略接口
│   └── metadata.py      # 工具元数据定义
│
├── L3.3: Tool Implementations（工具实现）
│   ├── primitive/       # 基础工具（HTTP/文件/数据库）
│   │   ├── http_tool.py
│   │   │   ├── http_get(url, headers?, timeout?) → {status, body, headers}
│   │   │   ├── http_post(url, body?, headers?, timeout?)
│   │   │   ├── http_put(url, body?, headers?, timeout?)
│   │   │   ├── http_delete(url, headers?, timeout?)
│   │   │   └── http_patch(url, body?, headers?, timeout?)
│   │   ├── file_tool.py
│   │   │   ├── file_read(path, encoding?) → {success, data}
│   │   │   ├── file_write(path, content, encoding?)
│   │   │   ├── file_delete(path)
│   │   │   ├── file_exists(path) → bool
│   │   │   ├── file_list(dir_path, pattern?)
│   │   │   └── file_mkdir(path)
│   │   └── db_tool.py
│   │       ├── db_query(sql, params?, connection?)
│   │       ├── db_execute(sql, params?, connection?)
│   │       ├── db_transaction(queries[])
│   │       └── db_schema(table_name)
│   │
│   ├── skill/           # Skill 工具（原子技能）
│   │   ├── browser/
│   │   │   ├── navigate.py      # navigate(url)
│   │   │   ├── snapshot.py     # snapshot() → screenshot
│   │   │   ├── click.py        # click(selector)
│   │   │   └── type.py         # type(selector, text)
│   │   ├── code/
│   │   │   ├── read.py         # read(file_path, offset?, limit?)
│   │   │   ├── write.py        # write(file_path, content)
│   │   │   └── search.py       # search(pattern, path?)
│   │   └── memory/
│   │       ├── recall.py       # recall(query, scope?)
│   │       └── store.py        # store(item)
│   │
│   └── composite/       # 组合工具（多个 primitive 组合）
│       ├── browser_flow.py    # navigate + snapshot + click + type
│       └── code_review.py     # read + search + write + comment
│
└── L3.4: Tool Catalog（工具目录）
    └── catalog.py       # 按 category/tag 索引工具
```

### 接口定义

```python
# L3.2: Tool Interface

from typing import Protocol, Any
from dataclasses import dataclass
from enum import Enum

class ToolScope(Enum):
    """工具作用域"""
    PRIMITIVE = "primitive"      # 原子工具
    SKILL = "skill"             # 技能工具
    COMPOSITE = "composite"     # 组合工具

@dataclass
class ToolMetadata:
    name: str
    scope: ToolScope
    category: str
    description: str
    parameters: dict[str, Any]
    tags: list[str]

class ToolRegistry(Protocol):
    """工具注册表接口"""
    def register(self, tool: ToolMetadata, handler: Callable) -> None: ...
    def get(self, name: str) -> ToolMetadata | None: ...
    def list_by_scope(self, scope: ToolScope) -> list[ToolMetadata]: ...
    def list_by_category(self, category: str) -> list[ToolMetadata]: ...

class ToolGuardrail(Protocol):
    """安全防护接口"""
    def check(self, tool: ToolMetadata, params: dict[str, Any]) -> CheckResult: ...
    def filter_result(self, result: Any) -> Any: ...
```

### ToolCall 协议

```python
# L3.1: executor.py

@dataclass
class ToolCall:
    """工具调用请求"""
    id: str
    name: str
    params: dict[str, Any]
    scope: ToolScope
    timeout_ms: int = 5000

@dataclass  
class ToolResult:
    """工具执行结果"""
    id: str
    status: Literal["success", "error", "timeout"]
    output: Any
    error: str | None
    execution_ms: int

class ToolExecutor:
    """执行器：统一调度 primitive/skill/composite"""
    
    def __init__(self, registry: ToolRegistry, guardrail: ToolGuardrail):
        self.registry = registry
        self.guardrail = guardrail
    
    async def execute(self, call: ToolCall) -> ToolResult:
        metadata = self.registry.get(call.name)
        check = self.guardrail.check(metadata, call.params)
        if not check.allowed:
            return ToolResult(id=call.id, status="error", ...)
        
        if metadata.scope == ToolScope.SKILL:
            return await self._execute_skill(call, metadata)
        elif metadata.scope == ToolScope.COMPOSITE:
            return await self._execute_composite(call, metadata)
        else:
            return await self._execute_primitive(call, metadata)
```

### 分层依赖关系

```
L3.4 Catalog ──→ L3.2 Registry（查询）
L3.2 Interface ──→ L3.1 Executor（调用）
L3.3 Implementations ──→ L3.2 Registry（注册）
L3.1 Executor ──→ L3.3 Implementations（执行）
```

### 实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| L3.1 ToolKernel | ⏳ 待启动 | executor.py + result.py |
| L3.2 Interface | ⏳ 待启动 | registry.py + guardrail.py + metadata.py |
| L3.3.1 primitive | ✅ 设计完成 | http_tool/file_tool/db_tool（9个方法）|
| L3.3.2 skill | ✅ 设计完成 | browser/code/memory skill（10个方法）|
| L3.3.3 composite | ✅ 设计完成 | browser_flow/code_review（2个组合）|
| L3.4 Catalog | ⏳ 待启动 | category/tag 索引 |

---

## Agent Base Profile 组织方案

> 状态: **设计阶段** | 实现状态: 待启动
> 更新: 2026-04-11

### 设计目标

| 目标 | 说明 |
|------|------|
| System Prompt 可组合 | 不硬编码，按 Layer 插拔 |
| CA 六层映射 | L0-L5 对应 Context Architecture |
| 插件化 | Profile = Plugin，支持动态注册 |
| 可测试 | 每层 Profile 独立单元测试 |

### Profile = Layer Stack

```python
@dataclass
class LayerProfile:
    """单层 Profile"""
    layer: int                    # L0-L5
    source: str                   # 来源标识
    content: str                 # 实际内容
    priority: int                # 优先级（同层内）
    ttl_seconds: int | None      # 过期时间，None=永不过期
    tags: list[str]              # 标签，用于检索

class AgentBaseProfile:
    """Agent Base Profile = 多层 Stack"""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.layers: dict[int, list[LayerProfile]] = {0:[], 1:[], 2:[], 3:[], 4:[], 5:[]}
    
    def add_layer(self, layer: LayerProfile) -> None:
        self.layers[layer.layer].append(layer)
        self.layers[layer.layer].sort(key=lambda x: x.priority)
    
    def compile(self) -> str:
        """编译为完整 System Prompt"""
        lines = []
        for layer in range(6):
            for profile in self.layers.get(layer, []):
                lines.append(f"=== {profile.source} (L{layer}) ===")
                lines.append(profile.content)
                lines.append("")
        return "\n".join(lines)
```

### CA 六层映射（通用）

| Layer | 内容 | 注入时机 | 可覆盖 |
|-------|------|---------|--------|
| **L0** | Model Identity | 固定 | ❌ |
| **L1** | Tool/Skill Descriptions | 启动时 | ⚠️ 部分 |
| **L2** | Behavioral Rules (SOUL/AGENTS) | 启动时 | ✅ |
| **L3** | Memory & Context | 每轮前 | ✅ |
| **L4** | Environment State | 每轮前 | ✅ |
| **L5** | Conversation History | 动态 | ✅ |

### CA 六层映射（Coding 场景优化）

| Layer | 内容 | 注入时机 | Coding 特有 |
|-------|------|---------|------------|
| **L0** | Model Identity | 固定 | 内置 CoT + 工具调用能力 |
| **L1** | Tool Descriptions | 启动时 | 含 risk_level + sideline_required 标记 |
| **L2** | Behavioral Rules | 启动时 | Coding SOUL：先规划后代码、小步提交、error→分析 |
| **L3** | Memory & Context | 每轮前 | 项目结构图 + 变更历史 + **Pit Fail** + 测试状态 |
| **L4** | Environment State | 每轮前 | git status/diff + linter + 测试覆盖率 |
| **L5** | Conversation History | 动态 | 多轮迭代 + 错误链 |

### L3 Memory & Context 完整结构

```
L3: Memory & Context
├── 项目结构图（文件树 + 最近变更）
├── 最近变更历史（last_k=10 diffs）
├── 相关文件内容摘要
├── 测试状态（pass/fail/error）
│
├── ⭐ Pit Fail（踩坑档案）
│   ├── file_path: 哪个文件
│   ├── error_type: import_cycle / null_ref / race_condition / ...
│   ├── symptom: 表现什么错误
│   ├── root_cause: 根因
│   ├── fix: 怎么修的
│   ├── recurrence_count: 复现次数
│   └── tags: ["python", "async", "import", "deadlock"]
│
└── 召回时：ContextCompiler.recall() 返回 relevant subset
```

### 可插拔 Profile Registry

```python
class ProfilePlugin(Protocol):
    """Profile 插件接口"""
    name: str
    layer: int
    
    async def get_content(self, context: ExecutionContext) -> str: ...
    async def validate(self, content: str) -> bool: ...

class ProfileRegistry:
    """可插拔 Profile 注册表"""
    
    def __init__(self):
        self._plugins: dict[str, ProfilePlugin] = {}
        self._static: dict[int, list[LayerProfile]] = defaultdict(list)
    
    def register_plugin(self, plugin: ProfilePlugin) -> None:
        self._plugins[plugin.name] = plugin
    
    def add_static(self, layer: int, profile: LayerProfile) -> None:
        self._static[layer].append(profile)
    
    async def build_profile(self, agent_id: str, context: ExecutionContext) -> AgentBaseProfile:
        profile = AgentBaseProfile(agent_id)
        
        # 1. 静态 Profile
        for layer, profiles in self._static.items():
            for p in profiles:
                profile.add_layer(p)
        
        # 2. 插件 Profile
        for plugin in self._plugins.values():
            content = await plugin.get_content(context)
            profile.add_layer(LayerProfile(
                layer=plugin.layer,
                source=plugin.name,
                content=content,
                priority=0,
                ttl_seconds=None,
                tags=[]
            ))
        
        return profile
```

### 插件示例

```python
# Memory Recall 插件 (L3)
class MemoryRecallPlugin:
    name = "memory_recall"
    layer = 3
    
    async def get_content(self, context: ExecutionContext) -> str:
        memories = await context.memory_service.recall(query=context.task, top_k=5)
        if not memories:
            return ""
        return "相关记忆：\n" + "\n".join(m.content for m in memories)

# Behavioral Rules 插件 (L2)
class SoulPlugin:
    name = "soul_rules"
    layer = 2
    
    async def get_content(self, context: ExecutionContext) -> str:
        soul_md = await context.workspace.read("SOUL.md")
        return soul_md
```

### 配置文件示例

```yaml
# agent_profiles.yaml

profiles:
  default:
    layer_0:
      - source: "model_identity"
        content: "你是一个有帮助的 AI 助手"
        priority: 0
    
    layer_2:
      - source: "soul"
        file: "SOUL.md"
        priority: 10
      - source: "agents"
        file: "AGENTS.md"
        priority: 20

  research:
    layer_2:
      - source: "soul"
        file: "SOUL.md"
        priority: 10
      - source: "research_rules"
        content: "注重事实核查和引用..."
        priority: 15
```

### 实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| LayerProfile 数据类 | ⏳ 待启动 | 定义 6 层结构 |
| AgentBaseProfile | ⏳ 待启动 | compile() 方法 |
| ProfileRegistry | ⏳ 待启动 | 插件注册表 |
| ProfilePlugin Protocol | ⏳ 待启动 | 插件接口 |
| MemoryRecallPlugin | ⏳ 待启动 | L3 动态记忆 |
| SoulPlugin | ⏳ 待启动 | L2 SOUL.md |
| YAML 配置 | ⏳ 待启动 | profiles.yaml |

---

## 蝴蝶模型：完整链路扩展

> 状态: **设计阶段** | 实现状态: 待启动
> 更新: 2026-04-11

### 蝴蝶模型本质
```
正向翼: 归纳、扩散、聚合（输入 → 泛化）
反向翼: 锚定、验证、收缩（泛化 → 具体）
双向蝴蝶联想贯穿所有层级
```

### 蝴蝶数据模型

```python
@dataclass
class WingMetadata:
    """蝴蝶元数据"""
    wing: Literal["forward", "backward", "bidirectional"]
    strength: float = 1.0        # 联想强度 0-1
    confidence: float = 0.5      # 置信度
    expires_at: datetime | None  # 蝴蝶效应过期

class Butterflyable(Protocol):
    """可蝴蝶化的接口"""
    @property
    def wing_metadata(self) -> WingMetadata: ...
    def get_forward_associations(self) -> list[str]: ...
    def get_backward_associations(self) -> list[str]: ...
```

---

### Memory System 蝴蝶链路

#### MemoryItem 蝴蝶扩展

```python
@dataclass
class MemoryItem:
    id: str
    content: str
    memory_type: MemoryType
    scope: MemoryScope
    importance_score: float
    
    # 🦋 蝴蝶扩展
    wing_metadata: WingMetadata
    forward_links: list[str] = []   # 正向联想 → 归纳路径
    backward_links: list[str] = []  # 反向联想 → 锚定路径
```

#### MemoryService 蝴蝶链路

```python
class MemoryService:
    """蝴蝶感知的记忆服务"""
    
    async def recall(
        self, 
        query: str, 
        wing: str | None = None  # 🦋 按翼筛选
    ) -> list[MemoryItem]:
        items = await self._base_recall(query)
        
        if wing == "forward":
            return [i for i in items if i.wing_metadata.wing in ["forward", "bidirectional"]]
        elif wing == "backward":
            return [i for i in items if i.wing_metadata.wing in ["backward", "bidirectional"]]
        return items
    
    async def store(self, item: MemoryItem) -> MemoryRef:
        # 🦋 自动建立蝴蝶联想
        associations = await self._compute_associations(item)
        item.forward_links = associations["forward"]
        item.backward_links = associations["backward"]
        return await self._base_store(item)
```

#### ContextCompiler 蝴蝶链路

```python
class ContextCompiler:
    """蝴蝶感知的上下文编译"""
    
    async def compile(
        self, 
        task: str, 
        wing_balance: tuple[int, int] = (1, 1)  # (正向权重, 反向权重)
    ) -> CompiledContext:
        if wing_balance[0] > 0:
            macro_memories = await self.memory.recall(task, wing="forward")
        if wing_balance[1] > 0:
            micro_memories = await self.memory.recall(task, wing="backward")
        
        # 🦋 保持双向翼平衡
        return self._balance_wings(macro_memories, micro_memories, wing_balance)
```

---

### Layer 3 Tool Execution 蝴蝶链路

#### ToolMetadata 蝴蝶扩展

```python
@dataclass
class ToolMetadata:
    name: str
    scope: ToolScope
    category: str
    
    # 🦋 蝴蝶扩展
    wing: Literal["forward", "backward"]  # 主要翼
    complementary_wing: str | None         # 互补翼
    forward_tags: list[str] = []          # 正向联想标签
    backward_tags: list[str] = []          # 反向联想标签
```

#### ToolRegistry 蝴蝶链路

```python
class ToolRegistry:
    """蝴蝶感知的工具注册表"""
    
    def register(
        self, 
        tool: ToolMetadata, 
        handler: Callable,
        forward_associations: list[str] = [],  # 🦋
        backward_associations: list[str] = []   # 🦋
    ):
        self._tools[tool.name] = {
            "handler": handler,
            "metadata": tool,
            "forward_links": forward_associations,
            "backward_links": backward_associations
        }
    
    def find_by_wing(self, wing: str) -> list[ToolMetadata]:
        return [t for t in self._tools.values() if t["metadata"].wing == wing]
```

#### ToolCatalog 蝴蝶聚合

```python
class ToolCatalog:
    """蝴蝶感知的工具目录"""
    
    def get_association_graph(self) -> dict:
        """🦋 生成联想图（用于前端边渲染）"""
        edges = []
        for name, data in self.registry._tools.items():
            for fwd in data["forward_links"]:
                edges.append({"source": name, "target": fwd, "wing": "forward"})
            for bwd in data["backward_links"]:
                edges.append({"source": name, "target": bwd, "wing": "backward"})
        return {"nodes": list(self.registry._tools.keys()), "edges": edges}
```

#### ToolExecutor 蝴蝶链路

```python
class ToolExecutor:
    """蝴蝶感知的执行器"""
    
    async def execute(self, call: ToolCall, wing_hint: str | None = None) -> ToolResult:
        metadata = self.registry.get(call.name)
        
        # 🦋 反向翼优先验证
        if metadata.wing == "backward":
            guard_result = await self.guardrail.verify(call, metadata)
            if not guard_result.allowed:
                return ToolResult(id=call.id, status="error", error=guard_result.reason)
        
        result = await self._dispatch_execute(call, metadata)
        
        # 🦋 正向翼后置：记录联想
        if metadata.wing == "forward":
            await self._propagate_forward(call, result)
        
        return result
```

---

### Agent Base Profile 蝴蝶链路

#### LayerProfile 蝴蝶扩展

```python
@dataclass
class LayerProfile:
    layer: int
    source: str
    content: str
    priority: int
    
    # 🦋 蝴蝶扩展
    wing: Literal["forward", "backward", "both"]
    induction_paths: list[str] = []    # 正向翼归纳路径
    anchor_constraints: list[str] = [] # 反向翼锚定约束
```

#### ProfileRegistry 蝴蝶链路

```python
class ProfileRegistry:
    """蝴蝶感知的 Profile 注册表"""
    
    def get_wing_visualization(self, agent_id: str) -> dict:
        """🦋 生成蝴蝶可视化数据（给前端）"""
        profile = self._profiles[agent_id]
        layers = {}
        for layer_num, profiles in profile.layers.items():
            layers[layer_num] = {
                "forward": [p for p in profiles if p.wing in ["forward", "both"]],
                "backward": [p for p in profiles if p.wing in ["backward", "both"]]
            }
        return {"agent_id": agent_id, "layers": layers, "balance_score": self._compute_balance(profile)}
```

---

### Frontend Canvas 蝴蝶链路

#### ButterflyNode 数据结构

```python
@dataclass
class ButterflyNode:
    id: str
    type: str
    position: tuple[float, float]
    data: dict
    
    # 🦋 蝴蝶扩展
    wing: Literal["forward", "backward", "both"]
    wing_strength: float = 1.0
    forward_connections: list[str] = []
    backward_connections: list[str] = []
    novelty_score: float = 0.5
```

#### ButterflyEdge 数据结构

```python
@dataclass
class ButterflyEdge:
    """蝴蝶边：双向联想"""
    id: str
    source: str
    target: str
    wing: Literal["forward", "backward", "bidirectional"]
    strength: float  # 0-1, 边的粗细
    animated: bool = False  # 强联想动画
    
    def should_render_in_view(self, view_mode: str) -> bool:
        if view_mode == "macro":
            return self.wing in ["forward", "bidirectional"]
        if view_mode == "micro":
            return self.wing in ["backward", "bidirectional"]
        return True
```

#### CanvasState 蝴蝶状态

```python
class CanvasState:
    """🦋 Zustand 蝴蝶状态"""
    wing_view: Literal["forward", "backward", "both", "balanced"] = "both"
    show_associations: bool = True
    association_threshold: float = 0.3
    
    def filter_edges(self, edges: list[ButterflyEdge]) -> list[ButterflyEdge]:
        return [
            e for e in edges 
            if e.should_render_in_view(self.wing_view)
            and e.strength >= self.association_threshold
        ]
```

#### ButterflyCanvas 渲染

```python
class ButterflyCanvas:
    """蝴蝶画布组件"""
    
    def render_nodes(self):
        for node in self.nodes:
            wing_color = {
                "forward": "#4CAF50",   # 绿色 = 归纳
                "backward": "#2196F3",    # 蓝色 = 锚定
                "both": "#9C27B0"         # 紫色 = 双向
            }[node.wing]
            opacity = 0.3 + (node.novelty_score * 0.7)
            # 渲染节点...
    
    def render_edges(self):
        for edge in self.filter_edges(self.edges):
            # 🦋 边粗细 = 联想强度
            # 🦋 边颜色 = 翼类型
            # 🦋 动画 = 强联想
            ReactFlowEdge(
                animated=edge.animated,
                style={"strokeWidth": edge.strength * 3}
            )
```

---

### CommunicationBus 蝴蝶链路

#### ButterflyMessage + ButterflyScope

```python
@dataclass
class ButterflyMessage:
    sender: str
    receiver: str
    content: str
    wing: Literal["forward", "backward"]
    association_strength: float = 0.5
    requires_response: bool = False

class ButterflyScope:
    """🦋 蝴蝶域：扩展信任域"""
    WING_DOMAIN = {
        "forward": ["kairos", "dreamer"],
        "backward": ["verifier", "critic"],
    }
    
    def can_communicate(self, sender: str, receiver: str, wing: str) -> bool:
        sender_domain = self.get_domain(sender)
        receiver_domain = self.get_domain(receiver)
        if sender_domain == receiver_domain:
            return True
        if wing == "forward" and receiver_domain in self.WING_DOMAIN["forward"]:
            return True
        if wing == "backward" and receiver_domain in self.WING_DOMAIN["backward"]:
            return True
        return False
```

---

### 蝴蝶链路图

```
🦋 正向翼路径（归纳、扩散）
──────────────────────────────────────────────────────────────
上游                   中游                      下游
────────────────────────────────────────────────────────────────────────────
MemoryItem          ContextCompiler         ButterflyCanvas
  wing_metadata → compile(wing=forward) → butterfly_nodes (forward)
  forward_links → _propagate_forward() → butterfly_edges (forward)

🦋 反向翼路径（锚定、验证）
──────────────────────────────────────────────────────────────
上游                   中游                      下游
────────────────────────────────────────────────────────────────────────────
MemoryItem          ContextCompiler         ButterflyCanvas
  wing_metadata → compile(wing=backward) → butterfly_nodes (backward)
  backward_links → _anchor_context() → butterfly_edges (backward)

🦋 蝴蝶平衡（ContextCompiler 协调）
──────────────────────────────────────────────────────────────
forward_memories ──┐
                   ├──→ _balance_wings() ──→ CompiledContext ──→ ButterflyCanvas
backward_memories ─┘                        ↓                              ↓
                                      wing_balance               node.opacity, edge.width
```

### 蝴蝶扩展：依赖变更摘要

| 层级 | 上游变更 | 下游变更 |
|------|---------|---------|
| **Memory** | WingMetadata + links | ContextCompiler + KG |
| **ContextCompiler** | wing_balance 参数 | Canvas butterfly viz |
| **Profile** | wing + induction/anchor | Canvas profile panel |
| **ToolExecutor** | wing dispatch | Canvas tool catalog |
| **ToolRegistry** | forward/backward links | ToolCatalog butterfly |
| **CommunicationBus** | ButterflyMessage + scope | Butterfly agents |
| **Canvas** | ButterflyNode + Edge | User visualization |

### 蝴蝶扩展：实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| WingMetadata | ⏳ 待启动 | 蝴蝶元数据基类 |
| Butterflyable Protocol | ⏳ 待启动 | 可蝴蝶化接口 |
| MemoryItem 蝴蝶扩展 | ⏳ 待启动 | wing_metadata + links |
| MemoryService 蝴蝶 | ⏳ 待启动 | recall/store 翼参数 |
| ContextCompiler 蝴蝶 | ⏳ 待启动 | wing_balance 参数 |
| ToolMetadata 蝴蝶 | ⏳ 待启动 | wing + tags |
| ToolRegistry 蝴蝶 | ⏳ 待启动 | find_by_wing() |
| ToolCatalog 蝴蝶 | ⏳ 待启动 | get_association_graph() |
| ToolExecutor 蝴蝶 | ⏳ 待启动 | wing dispatch |
| LayerProfile 蝴蝶 | ⏳ 待启动 | wing + paths |
| ProfileRegistry 蝴蝶 | ⏳ 待启动 | get_wing_visualization() |
| ButterflyNode | ⏳ 待启动 | 前端蝴蝶节点 |
| ButterflyEdge | ⏳ 待启动 | 前端蝴蝶边 |
| CanvasState 蝴蝶 | ⏳ 待启动 | wing_view + threshold |
| ButterflyCanvas | ⏳ 待启动 | 蝴蝶渲染组件 |
| ButterflyMessage | ⏳ 待启动 | 蝴蝶消息类型 |
| ButterflyScope | ⏳ 待启动 | 蝴蝶域隔离 |

---

## 设计决策记录（续）

### D-13: Layer 3 工具执行分层抽象 ✅
- **决策**: L3.1 Kernel / L3.2 Interface / L3.3 Implementations / L3.4 Catalog 四层结构
- **依据**: Skill 可复用、接口可测试、ToolScope 可插拔
- **实现**: 待启动
- **状态**: 设计阶段

### D-14: Agent Base Profile 可插拔方案 ✅
- **决策**: Profile = Layer Stack (L0-L5)，Plugin 模式动态注册
- **依据**: System Prompt 不硬编码，CA 六层映射
- **实现**: 待启动
- **状态**: 设计阶段

### D-15: 蝴蝶模型完整链路扩展 🆕
- **决策**: 正向翼（归纳）↔ 反向翼（锚定）双向联想贯穿所有层级
- **依据**: 蝴蝶思考架构 — 正向翼/反向翼双向联想模型
- **链路**: Memory → ContextCompiler → Profile → ToolExecutor → Canvas → CommunicationBus
- **实现**: 待启动
- **状态**: 设计阶段

### D-16: Meta Agent 机制抽象 🆕
- **决策**: Meta Agent = Subagent + TeamAgent + Sandbox + Conditional Spawn + Parallel
- **依据**: Claude Code (/teams), OpenClaw (SABG), OpenCode (multi-agent)
- **区别**: Meta Agent 是层级管理，Side Agent 是平行旁路
- **实现**: 待启动
- **状态**: 设计阶段

### D-17: Side Agent 旁路机制 🆕
- **决策**: Side Agent = Kairos + Verifier + Dreamer + Critic（独立进程，CA 注入）
- **依据**: LIF 电位触发、时间触发、事件触发
- **影响方式**: 通过 CA Layer（慢/中/快通道）注入主 Agent
- **实现**: 待启动
- **状态**: 设计阶段

### D-18: Meta Agent 条件型任务生成 🆕
- **决策**: Conditional Spawn = Cron Spawn + Heartbeat Spawn + Event Spawn + Queue Spawn
- **依据**: 任务规模 scale，需要动态扩缩容
- **应用**: 周期性健康检查、任务队列、事件驱动扩缩容
- **实现**: 待启动
- **状态**: 设计阶段

### D-19: CA Coding 场景优化 🆕
- **决策**: Coding 场景使用专用 CA 六层映射
- **依据**: Coding 场景工具密集、快速迭代、项目上下文依赖、多文件联动
- **L1 特化**: Tool Descriptions 含 risk_level + sideline_required 标记
- **L2 特化**: Coding SOUL（先规划后代码、小步提交、error→分析）
- **L3 特化**: 项目结构图 + 变更历史 + Pit Fail + 测试状态
- **L4 特化**: git status/diff + linter + 测试覆盖率
- **实现**: 待启动
- **状态**: 设计阶段

### D-20: Pit Fail 踩坑档案 🆕
- **决策**: L3 Memory 包含 Pit Fail，记录踩坑的根因和修复方案
- **依据**: Claude Code `remember` 理念，防止同类错误重复发生
- **组织**: file_path + error_type + symptom + root_cause + fix + recurrence_count
- **召回**: ContextCompiler.recall() 时返回 relevant Pit Fail subset
- **注入时机**: 每次 tool_call 产生前，Verifier 可见历史坑点
- **实现**: 待启动
- **状态**: 设计阶段

### D-21: Sideline Tool Call 控制层次 🆕
- **决策**: Sideline 对 Tool Call 的三层控制：约束(Guardrail) → 拦截(Interception) → 推理(Reasoning)
- **依据**: Harness 理念 — LLM=发动机，CA=传动系统，Sideline=驾驶员
- **约束层**: L1 Tool Descriptions 过滤工具清单
- **拦截层**: L3.1 Executor 执行前 Sideline 审查
- **推理层**: Sideline Agent 用独立 LLM 评估本次调用合理性
- **Provider Spec**: 在 L1 ToolMetadata.constraints 中嵌入 Provider 定义的约束
- **实现**: 待启动
- **状态**: 设计阶段

### D-22: engine.py 重构拆分 ✅ (2026-04-12)
- **决策**: engine.py (1143行) 拆分为 api/routes/ + services/
- **依据**: God Object 反模式 — 混了 HTTP handler、LLM Client、业务逻辑、Graph 构建
- **新结构**:
  - `engine.py` → 184行 (FastAPI app init + lifecycle)
  - `api/models.py` → Request/Response BaseModel
  - `api/routes/agents.py` → Agent CRUD
  - `api/routes/chat.py` → Chat + Execute SSE
  - `api/routes/memory.py` → Memory endpoints
  - `api/routes/entities.py` → KG entity endpoints
  - `services/agent_manager.py` → Agent lifecycle
  - `services/llm_client.py` → LLMClient (从 engine.py 移出)
  - `services/_state.py` → Shared state
- **提交**: `926ce71`
- **状态**: ✅ 已完成

### D-23: MemoryService 重构拆分 ✅ (2026-04-12)
- **决策**: MemoryService (622行) 拆分为 _recall/ + _crud.py + _blocks.py + _session.py
- **依据**: 单体类反模式 — CRUD + recall策略 + session管理 + permissions 全混在一起
- **新结构**:
  - `service.py` → 346行 (Facade，委托给子组件)
  - `_crud.py` → store/get/update/delete 操作
  - `_blocks.py` → Core Memory Block 操作
  - `_session.py` → Session 生命周期
  - `_recall/base.py` → RecallStrategy ABC
  - `_recall/keyword_recall.py` → 关键词召回
  - `_recall/semantic_recall.py` → 向量召回
  - `_recall/kg_recall.py` → 知识图谱召回
  - `_recall/shared_recall.py` → 共享作用域召回
- **提交**: `93eaf06`
- **状态**: ✅ 已完成

### D-24: L3.3 Implementations 设计定义 ✅ (2026-04-13)
- **决策**: L3.3 Implementations 分三层：primitive/skill/composite
- **primitive** (基础工具):
  - `http_tool.py` → http_get/post/put/delete/patch（5个方法）
  - `file_tool.py` → file_read/write/delete/exists/list/mkdir（6个方法）
  - `db_tool.py` → db_query/execute/transaction/schema（4个方法）
- **skill** (技能工具):
  - `browser/` → navigate/snapshot/click/type（4个方法）
  - `code/` → read/write/search（3个方法）
  - `memory/` → recall/store（2个方法）
- **composite** (组合工具):
  - `browser_flow.py` → navigate + snapshot + click + type
  - `code_review.py` → read + search + write + comment
- **状态**: ✅ 设计完成，⏳ 待实现

### D-25: 三层记忆架构 + Sideline Memory Agent ✅ (2026-04-13)
- **决策**: Forward 分层召回 + Backward Main 显式写回 + Sideline Memory Agent 独立管理
- **灵感来源**: Hermes LLM-wiki 明文记忆 + RAG + KG 混合

#### 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│  Forward (Context 构建)                                           │
│                                                                  │
│  Main Agent                                                      │
│  ├── Baseline: Wiki 明文搜索 (Hermes) ──→ 快速，短 context        │
│  │                                                            │
│  │    如果溢出 ──→ recall(query, mode=SEMANTIC)                │
│  │                          │                                  │
│  │                          ↓                                  │
│  │                   Sideline Memory                           │
│  │                   ├── Wiki 关系索引                         │
│  │                   ├── 向量检索 (RAG)                        │
│  │                   └── KG 扩展 (概念关系)                    │
│  │                          │                                  │
│  │                          ↓                                  │
│  │                   Sideline Verifier                        │
│  │                   └── recall 质量评分                       │
│  │                          │                                  │
│  └─────────────────────── Inject ◄─────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Backward (记忆写回)                                              │
│                                                                  │
│  Main Agent                                                      │
│  └── memory_write(item) ──→ Tools 调用                          │
│                                │                                 │
│                                ↓                                 │
│                         Sideline Memory                         │
│                         ├── wiki_write (明文)                  │
│                         ├── file_graph.sync (链接)              │
│                         ├── kg_add_relation (实体)               │
│                         └── vector_index.add (语义)              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Layer 1: Baseline (Hermes LLM-wiki 明文记忆)

```
~/agent-os/memory-wiki/
├── index.md                    # 分类导航
├── projects/
│   └── agent-os/
│       ├── index.md           # 项目总览
│       ├── decisions/         # 技术决策
│       │   └── D-22-engine-refactor.md
│       └── pitfails/          # 踩坑档案
├── agents/
│   └── memory-design.md
└── concepts/
    ├── context-compiler.md
    └── recall-strategy.md
```

| 特性 | 说明 |
|------|------|
| **LLM 自主** | prompt 规定何时读、读什么 |
| **高效** | 短 context 下无需向量检索 |
| **可解释** | 明文，可直接阅读 |

#### Layer 2: Sideline Memory (RAG + KG 混合)

| 子模块 | 技术 | 职责 |
|--------|------|------|
| **Wiki 索引** | 文件系统 + 链接解析 | 明文关系索引 |
| **文件图引擎** | Agent OS lightweight | 链接关系提取 |
| **向量索引** | HNSW/FAISS | 语义 chunk 检索 |
| **语义 KG** | SQLite KG | 概念关系推理 |

#### Layer 3: Sideline Verifier (recall 质量跟踪)

| 功能 | 说明 |
|------|------|
| **recall 评分** | 评估召回结果相关性、完整性 |
| **质量反馈** | 分数写入 KG，调整后续 recall 策略 |

#### Backward Tools (Main 调用)

| Tool | 说明 |
|------|------|
| `memory_write` | 写记忆到 wiki + KG + 向量索引 |
| `wiki_update` | 更新 wiki 页面 |
| `kg_add_relation` | 添加 KG 关系 |
| `file_graph_sync` | 同步文件图链接 |

#### 解耦价值

| 价值 | 说明 |
|------|------|
| **独立迭代** | Memory 可单独优化，不影响 Main |
| **版本管理** | 各子模块独立版本号 |
| **实验能力** | 可做 A/B 测试 |
| **技术演进** | 可替换 embedding 模型、KG 引擎 |

- **状态**: ✅ 设计完成，⏳ 待实现

---

## Coding 场景 CA IO 流程

### Coding 场景 CA Layer Stack

```
┌─────────────────────────────────────────────────────────────────┐
│ L0: Model                                                         │
│     内置推理 + CoT + 工具调用能力（固定不变）                        │
└─────────────────────────────────────────────────────────────────┘
                            ↓ 产生 tool_call
┌─────────────────────────────────────────────────────────────────┐
│ L1: Tool Descriptions (Coding Profile)                            │
│     - file_ops: read/write/mkdir/delete                          │
│     - shell: exec + working_dir                                  │
│     - git: status/diff/log/branch                                │
│     - browser: search_docs/pick                                  │
│     - skill: write_code/review_code/test_code                    │
│     - constraints: risk_level, sideline_required per tool         │
└─────────────────────────────────────────────────────────────────┘
                            ↓ LLM 产生 tool_call
┌─────────────────────────────────────────────────────────────────┐
│ L2: Behavioral Rules (Coding SOUL)                                │
│     - 先规划再写代码（think before code）                          │
│     - 小步提交，每次改动可回滚                                    │
│     - error → 先分析再修改                                        │
│     - 遇到不确定的 → 先验证再执行                                  │
└─────────────────────────────────────────────────────────────────┘
                            ↓ 每轮前编译
┌─────────────────────────────────────────────────────────────────┐
│ L3: Memory & Context                                              │
│     - 项目结构图（文件树 + 最近变更）                               │
│     - 最近变更历史（last_k=10 diffs）                              │
│     - 相关文件内容摘要                                             │
│     - 测试状态（pass/fail/error）                                 │
│     - ⭐ Pit Fail（踩坑档案）                                     │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ L4: Environment State                                             │
│     - git status/diff                                             │
│     - 当前 branch + recent commits                                 │
│     - linter/formatter 结果                                        │
│     - 测试覆盖率                                                   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ L5: Conversation History                                          │
│     - 多轮迭代上下文                                              │
│     - 错误上下文链                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Coding 场景 Sideline Agents

```
┌──────────────────────────────────────────────────────────────────┐
│ Sideline: Verifier（工具调用审查）                                  │
│                                                                   │
│ 触发条件：tool_call 产生后                                         │
│ 输入：tool_name + args + 当前项目上下文 + Pit Fail                  │
│ 输出：                                                             │
│   - verdict: "proceed" | "block" | "modify"                     │
│   - reason: 为什么阻止/修改                                        │
│   - suggestion: 如果 block，给出替代方案                            │
│                                                                   │
│ 决策规则：                                                         │
│   risk_level=CRITICAL → 强制 block（删除文件等）                  │
│   risk_level=HIGH → block + verify needed                         │
│   有测试未通过 → warn + 建议先修测试                               │
│   Pit Fail 中有相关历史坑 → warn + 提示历史修复方案               │
└──────────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ Sideline: Planner（任务规划）                                      │
│                                                                   │
│ 触发条件：用户发送新任务 / 当前任务完成                              │
│ 输入：用户目标 + 当前项目状态                                        │
│ 输出：                                                             │
│   - 子任务拆分（step_1, step_2, ...）                             │
│   - 依赖关系图                                                     │
│   - 预估风险点                                                     │
│                                                                   │
│ 工具：使用 LLM 独立做规划推理（不是直接调用工具）                   │
└──────────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────────┐
│ Sideline: Critic（代码质量）                                       │
│                                                                   │
│ 触发条件：代码写入后 / 代码审查请求                                  │
│ 输入：新代码 diff + 相关上下文                                       │
│ 输出：                                                             │
│   - 问题列表（security/style/logic/best practice）                  │
│   - 建议优先级                                                     │
│   - 可直接执行的修复建议                                           │
└──────────────────────────────────────────────────────────────────┘
```

### Coding 场景完整 IO 流程

```
User Input: "重构 auth 模块，分离 user 和 admin"
        ↓
┌─────────────────────────────────────┐
│ L3: 召回相关记忆                      │
│   - auth 模块现有结构                  │
│   - auth 相关的 Pit Fail（如果有）    │
│     - "import cycle 导致 circular import"
│     - "admin route 漏了权限检查"
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ Planner: 规划任务拆分                │
│   Step 1: 分析现有 auth 结构         │
│   Step 2: 设计 user/admin 分离方案   │
│   Step 3: 实施                      │
│   Step 4: 写测试                    │
│   Step 5: 验证                      │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ LLM: 产生 tool_call (read auth.py)  │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ Verifier: 审查 tool_call            │
│   tool: file_read                   │
│   args: path="auth.py"             │
│   Pit Fail 命中: import_cycle 历史   │
│   verdict: proceed ✅               │
│   warning: 注意之前有 import cycle 坑 │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ Executor: 执行工具                   │
│   file_read() → 返回内容             │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ Critic: 审查代码变更                 │
│   diff: 提出了分离方案               │
│   issues: 建议添加 migration 脚本   │
│   priority: MEDIUM                  │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│ L5: 追加到历史                       │
│ L2: 更新项目上下文                  │
│ L3: 写入本次变更到变更历史           │
└─────────────────────────────────────┘
```

---

## PitFail 数据结构

### PitFail 定义

```python
@dataclass
class PitFail:
    """踩坑档案 - 记录踩过的坑和修复方案"""
    
    id: str                                    # 唯一标识
    file_path: str                             # 哪个文件
    error_type: str                          # e.g. "import_cycle", "null_ref", "race_condition"
    symptom: str                              # 表现什么错误
    root_cause: str                           # 根因
    fix: str                                  # 怎么修的
    timestamp: datetime                        # 发现时间
    recurrence_count: int                     # 复现次数
    
    # 召回时用的标签
    tags: list[str]                           # ["python", "async", "import", "deadlock"]
    
    # 关联
    related_tool_calls: list[str]             # 相关的 tool_call 类型
    project_context: str                      # 项目上下文
```

### PitFail 召回机制

```python
class PitFailRegistry:
    """PitFail 注册表"""
    
    async def recall(self, file_path: str, tool_call: str | None = None) -> list[PitFail]:
        """召回与 file_path 或 tool_call 相关的 Pit Fail"""
        results = []
        
        # 1. 精确匹配文件路径
        results.extend(self._by_file.get(file_path, []))
        
        # 2. 工具调用关联
        if tool_call:
            results.extend(self._by_tool.get(tool_call, []))
        
        # 3. 标签模糊匹配
        for fail in self._all.values():
            if any(tag in file_path.lower() for tag in fail.tags):
                if fail not in results:
                    results.append(fail)
        
        # 4. 按 recurrence_count 排序（高频坑优先）
        results.sort(key=lambda x: x.recurrence_count, reverse=True)
        return results
    
    async def record(self, fail: PitFail) -> None:
        """记录新的 Pit Fail"""
        self._all[fail.id] = fail
        self._by_file.setdefault(fail.file_path, []).append(fail)
        for tool in fail.related_tool_calls:
            self._by_tool.setdefault(tool, []).append(fail)
```

### PitFail 与 Memory 层级关系

```
L2 Episodic Memory
  └── [session_1] "重构 auth → 踩了 import cycle 坑"
  └── [session_2] "加了 admin route → 漏了权限检查"

L3 Semantic Memory
  └── PitFail: "auth.py → import_cycle"
  └── PitFail: "auth.py → permission_miss"

L0 Working Memory
  └── 当前 task 召回的 PitFail (relevant subset)
      └── Verifier 审查时可见 + Planner 规划时可见
```

### PitFail 价值

| 场景 | 没有 Pit Fail | 有 Pit Fail |
|------|-------------|------------|
| 修同类 bug | 可能重蹈覆辙 | 直接看之前的修复方案 |
| 重构 | 可能引入历史 bug | 知道哪些地方容易出问题 |
| 代码审查 | 依赖 Critic 发现 | Critic + 历史教训双重保险 |
| 工具调用 | 不知道风险 | Verifier 审查时提示风险 |

