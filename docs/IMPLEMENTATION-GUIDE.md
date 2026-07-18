> ⚠️ **历史快照(gateway 已退役,2026-07-18)**:本文档含 `:8000` curl 引用指向已删除的 gateway BFF(commit c594969)。现役服务:orchestrator `:8001` / observe `:8002` / native `/h`。文中 `:8000` 示例为失效死引用,不再维护。

# Agent OS 详细实现指南

> 文档版本: 1.0.0
> 生成日期: 2026-04-17
> 项目路径: `~/projects/agent-os/`
> 核心审计评分: 9.5/10（Phase 0-9 全面审计通过）

---

## 目录

1. [项目概览](#1-项目概览)
2. [技术栈](#2-技术栈)
3. [架构总览](#3-架构总览)
4. [Orchestrator 核心模块](#4-orchestrator-核心模块)
5. [Memory 子系统](#5-memory-子系统)
6. [Context 上下文工程](#6-context-上下文工程)
7. [Tools 工具执行层](#7-tools-工具执行层)
8. [Communication 总线](#8-communication-总线)
9. [Concurrency 并发控制](#9-concurrency-并发控制)
10. [Skills 用户技能系统](#10-skills-用户技能系统)
11. [API 层](#11-api-层)
12. [前端画布](#12-前端画布)
13. [Phase 0-9 实施记录](#13-phase-0-9-实施记录)
14. [关键设计决策](#14-关键设计决策)
15. [文件结构索引](#15-文件结构索引)

---

## 1. 项目概览

### 1.1 项目简介

**Agent OS** 是一个面向 Agent 工作流的操作系统级平台，包含 5 大核心模块：

| 模块 | 说明 |
|------|------|
| **Agent 编排** | 生命周期管理、图状态机调度、多 Agent 协同 |
| **Prompt 管理** | 提示词模板引擎、版本控制 |
| **对话观测** | 多轮对话监控、上下文追踪、记忆管理 |
| **资源管理** | Provider 配置、模型路由、负载均衡 |
| **前端画布** | Flow UI — React Flow 可视化节点编辑器 |

### 1.2 项目位置

```
~/projects/agent-os/
```

### 1.3 服务架构

```
User → Next.js BFF (:3000) → API Gateway (:8000) → Python Microservices

Browser:  React Flow 可视化层 + Zustand 状态层
Backend:  Agent Orchestrator + 支持性微服务
```

### 1.4 微服务列表

默认运行栈（无 profile，`docker compose up` 即起）：

| 服务 | 端口 | 描述 |
|------|------|------|
| **web** | 3000 | Next.js 前端 + React Flow 画布 |
| **gateway** | 8000 | FastAPI API 网关（HTTP/SSE/WS） |
| **orchestrator** | 8001 | Agent 编排引擎（核心） |

辅助服务（`profiles: ["aux"]` 归档，默认不启动，需 `--profile aux` 显式拉起；未启动时 gateway 对其路由统一返回 502 兜底，可逆）：

| 服务 | 端口 | 描述 |
|------|------|------|
| **prompt-manager** | 8002 | Prompt 模板与版本管理 |
| **resource-manager** | 8004 | Provider 适配器与模型路由 |

### 1.5 核心代码统计

```
总代码行数（不含 node_modules/.venv）: ~15,338 行

Orchestrator (核心): ~9,000 行
  ├── engine.py + api/routes + services: ~1,000 行
  ├── graph/: ~700 行
  ├── memory/: ~4,200 行
  ├── tools/: ~250 行
  ├── communication/: ~800 行
  ├── concurrency/: ~410 行
  ├── skills/: ~1,800 行
  └── skill_catalog/: ~600 行

Gateway: ~1,000 行
Frontend (TypeScript): ~1,300 行
```

---

## 2. 技术栈

### 2.1 技术栈总表

| 层级 | 技术 |
|------|------|
| **前端** | React 18 + Next.js 15 (App Router) + React Flow + Zustand + TailwindCSS |
| **BFF** | Next.js API Routes |
| **API 网关** | FastAPI (Python) |
| **编排引擎** | Python 3.12 + asyncio + Graph State Machine |
| **进程间通信** | HTTP/SSE/WebSocket (gRPC 仅有 proto 骨架, packages/proto/, 运行时未接线) |
| **数据库（当前）** | SQLite + SQLiteStore |
| **向量索引** | FAISS (faiss-cpu) |
| **轻量 KG** | SQLite 两表（entities + relations） |
| **Embedding** | SentenceTransformer (本地模型) |
| **生产目标** | PostgreSQL + pgvector + Neo4j |

### 2.2 关键 Python 依赖

```
# 核心
fastapi / uvicorn
pydantic
asyncio

# 记忆系统
faiss-cpu / sentence-transformers
pydantic-ai（参考，不直接依赖）

# 前端
react / next.js / react-flow / zustand
```

---

## 3. 架构总览

### 3.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (React)                          │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│   │  FlowCanvas  │  │  AgentNode   │  │  ExecutionHistory    │ │
│   │  (React Flow)│  │  ToolNode    │  │  MemoryPanel         │ │
│   └──────┬──────────────┬──────────────┬────────────┐
       │              │              │
       ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────────┐
│Orchestrator│  │ Prompt  │  │  Resource    │
│  :8001    │  │ Manager │  │  Manager     │
└────┬─────┘  └──────────┘  └──────────────┘
     │
     ▼
┌────────────────────────────────────────────────────────────────┐
│              Orchestrator Core Engine (Python)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ Graph    │ │ Context  │ │ Memory   │ │ Tools    │          │
│  │ State    │ │ Compiler │ │ Service  │ │ Executor │          │
│  │ Machine  │ │          │ │          │ │          │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                       │
│  │Communica-│ │Concurren-│ │ Skills   │                       │
│  │tionBus   │ │cyControl │ │ Catalog  │                       │
│  └──────────┘ └──────────┘ └──────────┘                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌──────────┐        ┌──────────┐       ┌──────────┐
    │ SQLite   │        │  FAISS   │       │  SQLite  │
    │ Store    │        │ Vector   │       │  KG      │
    │ (memories│        │ Store    │       │ (entities│
    │  +blocks │        │          │       │ +relations
    │  +sessions│        │          │       │  )       │
    └──────────┘        └──────────┘       └──────────┘
```

### 3.2 Orchestrator 模块依赖图

```
engine.py (FastAPI 入口, 571 行)
    │
    ├── api/models.py (Pydantic 请求/响应模型)
    ├── api/routes/
    │   ├── agents.py (Agent CRUD)
    │   ├── chat.py (对话 + SSE 执行流)
    │   ├── memory.py (记忆 CRUD)
    │   └── entities.py (KG 实体)
    │
    ├── services/
    │   ├── agent_manager.py (Agent 生命周期)
    │   ├── llm_client.py (LLM 客户端封装)
    │   └── _state.py (全局共享状态)
    │
    ├── graph/                   [Phase 1 完成]
    │   ├── state.py (GraphState dataclass, 61 行)
    │   ├── nodes.py (GraphNode + LLMNode + ToolCallNode, 86 行)
    │   ├── edges.py (Edge + ConditionalEdge, 70 行)
    │   └── __init__.py (StateGraph + SubgraphNode + ParallelNode + FanInNode, 480 行)
    │
    ├── context/                 [Phase 3 完成]
    │   ├── compiler.py (ContextCompiler, 111 行)
    │   └── manager.py (ContextManager write/select/compress/isolate, 158 行)
    │
    ├── memory/                  [Phase 2 + Phase 7 完成]
    │   ├── types.py (MemoryItem/MemoryRef/MemoryBlock 等, 194 行)
    │   ├── store.py (InMemoryStore, 298 行)
    │   ├── sqlitestore.py (SQLiteStore, 505 行)
    │   ├── pgstore.py (PostgresStore, 565 行)
    │   ├── service.py (MemoryService 门面, 346 行)
    │   ├── compressor.py (异步/同步压缩 + ContextMonitor, 511 行)
    │   ├── forgetting.py (ActiveForgetting, 236 行)
    │   ├── migrator.py (MemoryMigrator L1→L2→L3, 376 行)
    │   ├── scorer.py (ImportanceScorer 五维度, 245 行)
    │   ├── knowledge_graph.py (轻量 KG, 849 行)
    │   ├── embedding.py (SentenceTransformerProvider, 73 行)
    │   ├── vector.py (FAISSVectorStore, 337 行)
    │   ├── permissions.py (PermissionManager, 345 行)
    │   ├── _blocks.py (BlockOperations, 40 行)
    │   ├── _crud.py (CrudOperations, 137 行)
    │   ├── _session.py (SessionOperations, 130 行)
    │   ├── _recall/
    │   │   ├── base.py (RecallStrategy ABC, 25 行)
    │   │   ├── keyword_recall.py (45 行)
    │   │   ├── semantic_recall.py (62 行)
    │   │   ├── kg_recall.py (61 行)
    │   │   └── shared_recall.py (55 行)
    │   └── sideline/
    │       ├── agent.py (旁路 Agent, 196 行)
    │       ├── baseline.py (基线, 174 行)
    │       ├── rag_engine.py (RAG 引擎, 253 行)
    │       └── verifier.py (Verifier, 161 行)
    │
    ├── tools/                   [Phase 4 完成]
    │   ├── executor.py (ToolExecutor 异步闭环, 125 行)
    │   ├── registry.py (ToolRegistry, 35 行)
    │   └── guardrail.py (Guardrail, 89 行)
    │
    ├── communication/          [Phase 8 完成]
    │   ├── bus.py (CommunicationBus, 425 行)
    │   ├── message.py (AgentMessage dataclass, 152 行)
    │   └── scope.py (ScopeManager 信任域, 227 行)
    │
    ├── concurrency/             [Phase 8 完成]
    │   └── controller.py (ConcurrencyController, 407 行)
    │
    └── skills/ + skill_catalog/ [D-26 完成]
        ├── skill_loader.py (243 行)
        ├── skill_executor.py (230 行)
        ├── skill_config.py (243 行)
        ├── skill_catalog.py (143 行)
        ├── prompt_integration.py (128 行)
        ├── tool_registry_integration.py (293 行)
        ├── hot_reload.py (388 行)
        ├── cli.py (401 行)
        └── skill_catalog/
            ├── loader.py (227 行)
            ├── registry.py (181 行)
            └── config.py (177 行)
```

---

## 4. Orchestrator 核心模块

### 4.1 engine.py — FastAPI 应用入口（571 行）

**文件**: `services/orchestrator/src/engine.py`

**职责**:
- FastAPI 应用初始化
- 全局共享状态初始化（`_state`）
- 生命周期钩子（startup/shutdown）
- 路由注册

**关键组件初始化顺序**:
```python
# 1. LLM 客户端
_state.llm_client = LLMClient()

# 2. KG 必须先于 MemoryService 创建（依赖顺序）
_state.knowledge_graph = KnowledgeGraph()
_state.embedding_provider = SentenceTransformerProvider()
_state.vector_store = FAISSVectorStore(provider=_state.embedding_provider)

# 3. MemoryService（依赖 KG + VectorStore）
_state.memory_service = MemoryService(
    SQLiteStore(),
    vector_store=_state.vector_store,
    knowledge_graph=_state.knowledge_graph
)

# 4. 压缩组件
_state.context_monitor = ContextMonitor()
_state.async_compressor = AsyncCompressor(monitor=_state.context_monitor)
_state.sync_compressor = SyncCompressor(monitor=_state.context_monitor)

# 5. 迁移 + 遗忘
_state.memory_migrator = MemoryMigrator(_state.memory_service)
_state.active_forgetting = ActiveForgetting(_state.memory_service)

# 6. Context + Tools + Communication + Concurrency
_state.context_manager = ContextManager(_state.memory_service)
_state.context_compiler = ContextCompiler(_state.context_manager)
_state.tool_executor = ToolExecutor(ToolRegistry())
_state.communication_bus = CommunicationBus()
_state.concurrency_controller = ConcurrencyController()
```

**Startup 钩子**:
- `init_default_agent_hook()`: 自动创建默认 Agent（若不存在）
- `_start_forgetting_sweep()`: 每 24 小时运行一次遗忘扫描 + L2→L3 迁移

**Shutdown 钩子**:
- FAISS 索引持久化
- PostgreSQL 连接关闭（若启用）
- CommunicationBus 资源清理

**可选 PostgreSQL 支持**: 设置 `DATABASE_URL` 环境变量启用 PostgresStore

---

### 4.2 graph/ — 图状态机（Phase 1 完成）

#### 4.2.1 state.py — GraphState（61 行）

**核心数据结构**:

```python
@dataclass
class GraphState:
    messages: list[dict]              # 对话历史
    current_node: str                  # 当前执行节点名
    context: dict                      # 节点间共享上下文
    status: str                        # idle/running/done/needs_tool/error

    agent_id: str
    session_id: str
    input: str
    output: str

    memory_refs: list[str]             # 存储的记忆引用
    tool_results: list[dict]          # 工具执行结果
    errors: list[str]                  # 累积错误
    metadata: dict                     # 额外元数据
    subgraph_results: dict             # 子图执行结果
    parallel_results: dict             # 并行分支结果
```

**关键方法**:
- `to_dict()` / `from_dict()`: 序列化（用于 checkpoint）
- `clone()`: 深拷贝（用于子图/并行分支隔离）

#### 4.2.2 nodes.py — 节点类型（86 行）

```python
class GraphNode(ABC):           # 抽象基类
    async def execute(state: GraphState) -> GraphState

class FunctionNode(GraphNode):   # 包装异步函数的便捷节点
    def __init__(self, name, handler: Callable)

class LLMNode(GraphNode):       # 模拟 LLM 调用节点（MVP 模式 echo）
class ToolCallNode(GraphNode):  # 模拟工具调用节点
```

#### 4.2.3 edges.py — 边路由（70 行）

```python
class Edge:                     # 无条件边
    def route(state) -> str     # 始终返回固定 target

class ConditionalEdge:           # 条件边（基于 state 字段路由）
    def __init__(self, source, targets, condition_field="status",
                 condition=None)
    def route(state) -> str     # 查表 + __default__ fallback
```

#### 4.2.4 `__init__.py` — StateGraph 主类（480 行）

**StateGraph**: 有向图编排器

**节点类型**:
| 节点 | 说明 |
|------|------|
| `SubgraphNode` | 嵌套另一个 StateGraph，支持 3 种 merge 策略（replace/append/context） |
| `ParallelNode` | 并发执行多分支，max_concurrency 信号量限制 |
| `FanInNode` | 等待并行分支完成，merge 策略：concat/best/aggregate |

**Checkpoints**:
- `InMemoryCheckpointStore`: 内存 checkpoint 存储
- 节点执行后自动保存 snapshot
- `resume()`: 从最新 checkpoint 恢复继续执行

**API**:
```python
graph = StateGraph("my-graph")
graph.add_node("start", node)
graph.add_edge("start", "process")
graph.add_conditional_edge("process", {"done": "end", "__default__": "retry"})
graph.set_entry_point("start")
result = await graph.run(initial_state, on_node_complete=cb)
```

---

## 5. Memory 子系统

> **Phase 2 (MVP) + Phase 7 (增强) 完成**
> 核心文件: `services/orchestrator/src/memory/`

### 5.1 四层记忆模型

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
│   持久化知识、实体关系、用户画像（SQLite KG）             │
│   存储: KG + 向量DB，访问: 图查询 + 混合检索             │
└─────────────────────────────────────────────────────────┘
```

### 5.2 核心类型（types.py, 194 行）

```python
class MemoryType(Enum):     # WORKING/SESSION/EPISODIC/SEMANTIC
class MemoryScope(Enum):     # AGENT/SESSION/WORKSPACE/GLOBAL
class RecallMode(Enum):      # KEYWORD/SEMANTIC/KG

@dataclass
class MemoryItem:
    id, content, memory_type, scope, importance,
    created_at, accessed_at, archived, metadata

@dataclass
class MemoryRef:             # 存储后的引用句柄
    id: str
    memory_type: MemoryType

@dataclass
class MemoryBlock:           # Core Memory（persona/user_profile）
    agent_id, label, content, char_limit
```

### 5.3 存储层

#### 5.3.1 InMemoryStore（store.py, 298 行）

纯内存实现，MVP 阶段使用：
- `store()` / `get()` / `update()` / `delete()`
- `list_by_scope()` / `get_block()` / `update_block()`
- Session/Block/MemoryItem 全内存索引

#### 5.3.2 SQLiteStore（sqlitestore.py, 505 行）

Phase 9 轻量持久化实现：
- **零外部依赖**: 仅 `sqlite3`（Python 内置）+ `faiss-cpu`
- **表结构**:
  - `memories`: id, agent_id, session_id, memory_type, scope, content, importance, metadata(JSON), created_at, accessed_at, archived
  - `memory_blocks`: agent_id, label, content, char_limit, updated_at
  - `sessions`: session_id, agent_id, status, created_at, archived_at
  - `memory_versions`: memory_id, version, content, operator, created_at
- **向后兼容**: 无 `data/` 目录时自动降级 InMemoryStore

#### 5.3.3 PostgresStore（pgstore.py, 565 行）

生产目标存储（当前可选）：
- 需设置 `DATABASE_URL` 环境变量启用
- 支持向量搜索（备选 pgvector 集成点）
- 连接池管理

### 5.4 向量系统

#### 5.4.1 SentenceTransformerProvider（embedding.py, 73 行）
- 基于 `sentence-transformers` 本地模型
- LRU 单例模式缓存

#### 5.4.2 FAISSVectorStore（vector.py, 337 行）
- `add_embeddings()`: 添加向量到 FAISS 索引
- `search()`: 余弦相似度检索
- `read_index()` / `save_index()`: FAISS 文件持久化
- 5 秒 debounce 写回磁盘

### 5.5 知识图谱（knowledge_graph.py, 849 行）

Phase 9 SQLite KG 实现（轻量版）：
- **entities 表**: id, name, entity_type, properties(JSON), valid_from, valid_to, created_at
- **relations 表**: id, from_entity, relation_type, to_entity, properties(JSON), valid_from, valid_to
- **递归 CTE**: 支持 N 跳图遍历查询
- **时序事实追踪**: valid_from/valid_to 字段支持事实历史版本
- **接口抽象化**: 预留 Neo4j 迁移路径

### 5.6 压缩引擎（compressor.py, 511 行）

双触发压缩机制：
| 饱和度 | 动作 | 阻塞 |
|--------|------|------|
| > 70% | 异步压缩（Observer 评估 → Reflector 摘要） | 否 |
| > 85% | 同步压缩（阻塞，2s 超时后跳过） | 是 |

**核心组件**:
- `ContextMonitor`: 监控 context 饱和度
- `AsyncCompressor`: 后台压缩，不阻塞推理
- `SyncCompressor`: 同步压缩，带 2s 超时保护
- `CompressionLevel`: TRIVIAL / LIGHT / MODERATE / AGGRESSIVE

### 5.7 遗忘机制（forgetting.py, 236 行）

工具结果生命周期：
- **安全期轮次计数器**（默认 4 轮 sweep 后解除保护）
- `run_sweep()`: 每次递减 `safety_turns_remaining`
- **评分驱动软删除**: importance < 阈值持续 N 天 → 软删除

### 5.8 迁移系统（migrator.py, 376 行）

层间迁移：
| 迁移 | 时机 | 保证 |
|------|------|------|
| L0 → L1 | 每轮推理结束 | 同步批量，不丢数据 |
| L1 → L2 | 会话结束 | 异步，checkpoint 恢复 |
| L2 → L3 | Heartbeat/Cron | 非阻塞，不抢推理资源 |

### 5.9 评分系统（scorer.py, 245 行）

五维度重要性评分：
```python
weights = {
    "recency": 0.25,        # 时间衰减
    "frequency": 0.15,       # 访问频率
    "relevance": 0.25,      # 查询相关性
    "emotional_weight": 0.15, # 情感权重
    "actionability": 0.20,  # 可操作度
}
```

**三种模板**:
- `coding`: actionability 优先（0.30）
- `research`: relevance 优先（0.40）
- `general`: 均衡分布

### 5.10 权限管理（permissions.py, 345 行）

5 级信任域隔离：
| 级别 | 描述 |
|------|------|
| 0 | 无访问 |
| 1 | 仅存在性 |
| 2 | 摘要可见 |
| 3 | 详情可见（需授权） |
| 4 | 可写 |

**Scope**: AGENT / SESSION / WORKSPACE / GLOBAL

### 5.11 Recall 策略（_recall/ 目录）

| 策略 | 文件 | 说明 |
|------|------|------|
| Keyword | keyword_recall.py | 关键词大小写不敏感匹配 |
| Semantic | semantic_recall.py | 向量余弦相似度 + 关键词重排 |
| KG | kg_recall.py | 知识图谱实体关联记忆 |
| Shared | shared_recall.py | 跨 Agent 共享记忆（权限过滤） |

### 5.12 Sideline Memory Agent（sideline/ 目录）

旁路 Agent 系统（Phase 8 增强）：

| Agent | 文件 | 职责 |
|-------|------|------|
| **Verifier** | verifier.py | Turn 边界验证，检测错误 |
| **Baseline** | baseline.py | 基线行为监控 |
| **RAG Engine** | rag_engine.py | RAG 检索增强 |
| **Sideline Agent** | agent.py | 旁路观察者角色 |

---

## 6. Context 上下文工程

> **Phase 3 完成**
> 文件: `services/orchestrator/src/context/`

### 6.1 ContextManager（manager.py, 158 行）

四策略上下文管理器：

```python
class ContextManager:
    async def write(session_id, entry)       # 上下文外部化到 MemoryService
    async def select(session_id, query)      # 从外部记忆召回
    async def compress(entries, target_tokens) # 按 importance+时间压缩
    async def isolate(parent_session_id)     # 创建子 Agent 隔离作用域
```

### 6.2 ContextCompiler（compiler.py, 111 行）

上下文编译（组装最终 LLM 输入）：

```python
class ContextCompiler:
    async def compile(
        system_prompt,
        conversation,
        agent_id,
        session_id,
        tool_defs=None,
        max_tokens=128_000,
    ) -> list[dict]:  # 返回 message 列表
```

**编译流程**:
1. System prompt 开头
2. `select()` 召回相关记忆 → 注入为 `[Relevant memories]` system 块
3. 工具定义 → 注入为 `[Available tools]` system 块
4. 对话历史（按 budget 截断）
5. Token 估计：约 4 字符/token

---

## 7. Tools 工具执行层

> **Phase 4 完成**
> 文件: `services/orchestrator/src/tools/`

### 7.1 ToolRegistry（registry.py, 35 行）

```python
class ToolRegistry:
    def register(name, handler, description="", parameters={})
    def get(name) -> dict | None
    def list_tools() -> list[dict]
```

### 7.2 ToolExecutor（executor.py, 125 行）

完整异步工具执行闭环：

```python
class ToolExecutor:
    async def execute(tool_name, arguments, timeout=30.0) -> dict:
        # 1. 查找工具
        # 2. Guardrail 输入检查
        # 3. asyncio.wait_for 执行（超时保护）
        # 4. Guardrail 输出检查
        # 5. 返回 {status, output, error, metadata}
```

### 7.3 Guardrail（guardrail.py, 89 行）

双阶段安全检查：

**输入检查**（DANGEROUS_PATTERNS）:
```
rm -rf /   del /s    format c:    ../（路径穿越）
sudo       chmod 777
```

**输出检查**（SENSITIVE_PATTERNS）:
```
password:=    api_key:=    secret:=    token:=
```

### 7.4 L3.3 工具实现（skills/ 子目录）

| 工具集 | 路径 | 说明 |
|--------|------|------|
| **Primitive** | `skills/primitive/` | HTTP/文件/数据库基础工具 |
| **Skill** | `skills/browser/`, `skills/code/`, `skills/memory/` | 原子技能 |
| **Composite** | `skills/tool_registry_integration.py` | 工具注册集成 |
| **Browser** | `skills/browser/_browser.py` | Playwright 真实集成（修复后） |

---

## 8. Communication 总线

> **Phase 8 完成**
> 文件: `services/orchestrator/src/communication/`

### 8.1 CommunicationBus（bus.py, 425 行）

**核心功能**:
| 方法 | 说明 |
|------|------|
| `send(message)` | 直接消息发送 |
| `broadcast(message, session_id, workspace_id)` | 广播 |
| `publish(topic, message)` | 主题发布/订阅 |
| `request(message, timeout)` | 请求/响应（correlation_id 关联） |
| `receive(agent_id, timeout)` | 消息接收（队列） |

**消息类型**: TASK / BROADCAST / REQUEST / RESPONSE / HEARTBEAT

**优先级**: CRITICAL(0) / HIGH(1) / NORMAL(2) / LOW(3)

### 8.2 AgentMessage（message.py, 152 行）

```python
@dataclass
class AgentMessage:
    id, sender_id, recipient_id, session_id, workspace_id
    message_type, content, payload
    correlation_id, timestamp, priority, ttl_seconds
    delivery_status  # PENDING/DELIVERED/ACKNOWLEDGED/EXPIRED
```

### 8.3 ScopeManager（scope.py, 227 行）

信任域管理（与 permissions.py 配合）:
- AGENT: 每个 Agent 独立队列
- SESSION: 同会话 Agent 广播
- WORKSPACE: 同项目 Agent 广播
- GLOBAL: 全局（KG 等）

---

## 9. Concurrency 并发控制

> **Phase 8 完成**
> 文件: `services/orchestrator/src/concurrency/controller.py`（407 行）

### 9.1 核心功能

| 功能 | 说明 |
|------|------|
| **Agent 槽位** | Semaphore 限制（默认 max_agents=10） |
| **Tool 槽位** | Semaphore 限制（默认 max_tools=20） |
| **优先级调度** | heapq 优先级队列（CRITICAL→LOW） |
| **依赖管理** | `add_dependency()`, `get_ready_tasks()` |
| **超时执行** | `execute_with_timeout()` |
| **任务取消** | `cancel_task()`, `cancel_all()` |
| **依赖感知执行** | 未满足依赖的任务自动延迟 |

### 9.2 并发模式

```
独立工具 → 并行执行（asyncio.gather）
有依赖 → 依赖完成后自动调度
Semaphore → 限制最大并发数
```

---

## 10. Skills 用户技能系统

> **D-26 完成**
> 文件: `services/orchestrator/src/skills/` + `services/orchestrator/src/skill_catalog/`

### 10.1 三层目录优先级

```
最高: ~/.agent-os/skills/<skill>/SKILL.md     （用户级）
中:   <project>/.agent-os/skills/<skill>/    （项目级）
最低: <pkg>/skills/<skill>/SKILL.md           （内置级）
```

### 10.2 SKILL.md 格式

```yaml
---
name: skill-name
description: 技能描述
version: 1.0.0
author: builtin|user
triggers:
  - /skill-name          # /命令触发
  - auto                 # 自动触发
exposure:
  visible: true          # 是否在 available_skills 列表显示
  userCallable: true     # 用户是否可主动调用
---

# 正文: Markdown 格式使用说明
```

### 10.3 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **SkillLoader** | `skill_loader.py` (243 行) | 扫描/解析 SKILL.md，YAML frontmatter 提取 |
| **SkillCatalog** | `skill_catalog.py` (143 行) | 全局 Skill 目录管理 |
| **SkillExecutor** | `skill_executor.py` (230 行) | Skill 执行（解析触发词→调用对应工具） |
| **SkillConfig** | `skill_config.py` (243 行) | 用户配置（JSON）持久化 |
| **SkillRegistry** | `skill_catalog/registry.py` (181 行) | Skill 元数据注册表 |
| **SkillCatalogLoader** | `skill_catalog/loader.py` (227 行) | 文件系统扫描加载 |
| **PromptIntegration** | `prompt_integration.py` (128 行) | `available_skills` 注入 Base Prompt |
| **ToolRegistryIntegration** | `tool_registry_integration.py` (293 行) | Skill → L3 ToolRegistry 转换 |
| **HotReload** | `hot_reload.py` (388 行) | Skill 文件热重载（轮询 + 事件驱动） |
| **CLI** | `cli.py` (401 行) | Skill 管理命令行工具 |

### 10.4 实现阶段（D-26）

| 阶段 | Commit | 内容 |
|------|--------|------|
| P0 | `2e85218` | SkillLoader + SkillCatalog |
| P1 | `6a20104` | SkillExecutor + SkillConfig |
| P2 | `bd88cb4` | ToolRegistry Bridge + Prompt 注入 |
| P3 | `31cdc97` | HotReload + CLI |
| Fix | `36ed514`等 | C-1/C-2/H-1/H-2/H-3 修复 |
| 验证 | `c6a4cb7` | hello-agent 验证通过 |

---

## 11. API 层

### 11.1 Gateway（services/gateway/, ~1,000 行）

**入口**: `main.py`（112 行）

**路由**:
| 路由 | 标签 | 说明 |
|------|------|------|
| `/auth/*` | auth | 认证（JWT RS256 + 旋转） |
| `/agents/*` | agents | Agent CRUD |
| `/chat` | chat | 对话 |
| `/execute` | execute | SSE 执行流 |
| `/memories/*` | memories | 记忆 CRUD |
| `/messages/*` | messages | 通信消息 |
| `/conversations/*` | conversations | 会话管理 |
| `/prompts/*` | prompts | Prompt 管理 |
| `/resources/*` | resources | 资源管理 |
| `/kg/*` | kg | 知识图谱 |
| `/debug/*` | debug | 调试端点 |
| `/health` | — | 健康检查 |

**中间件**:
- CORS（可配置 origins）
- JWT Auth（`require_auth` 依赖）
- Rate Limiting（token 旋转）

### 11.2 Orchestrator API（services/orchestrator/src/api/routes/）

| 文件 | 行数 | 说明 |
|------|------|------|
| `agents.py` | 58 | Agent CRUD |
| `chat.py` | 1031 | 对话 + SSE 执行流（核心） |
| `memory.py` | 851 | 记忆 CRUD + layers 统计 |
| `entities.py` | 84 | KG 实体查询 |
| `orchestrate.py` | 231 | 多 Agent 编排 + SSE (POST /v1/orchestrate) |
| `canvas.py` | 330 | 无尽画布 / replay (after_id) |
| `pitfail.py` | 80 | PitFail 端点 |

**chat.py 核心功能**:
- `_build_execution_graph()`: 构建执行图
- SSE 流推送: `node_start` / `node_complete` / `tool_call` / `tool_result` / `memory_event` / `error`
- KG 提取触发: 每次对话自动提取实体和关系
- 工具调用检测: 正则 `tool_call|function_call|action`

---

## 12. 前端画布

> **Phase 5 完成 + Phase 6 集成**
> 路径: `apps/web/src/`

### 12.1 技术栈

- **Next.js 15** (App Router)
- **React Flow** (`@xyflow/react`) — 节点编辑器
- **Zustand** — 状态管理
- **TailwindCSS** — 样式
- **TypeScript** — 类型安全

### 12.2 组件结构

```
components/canvas/
├── FlowCanvas.tsx        # React Flow 主画布（154 行）
├── PropertyPanel.tsx     # 节点属性编辑面板（278 行）
├── ExecutePanel.tsx      # 执行状态面板（109 行）
├── BranchManager.tsx     # Endless Canvas 分支管理
├── Layer2Panel.tsx       # Layer2 子面板
├── LODControl.tsx        # 细节层次控制
├── ScoringOverlay.tsx    # 评分叠加层
├── TabBar.tsx            # 标签栏
├── TickCanvas.tsx        # 实时画布（WebSocket）
├── edges/
│   └── DataEdge.tsx      # 数据边（连接线）
└── nodes/
    ├── AgentNode.tsx     # Agent 节点（39 行）
    ├── ToolNode.tsx      # Tool 节点（28 行）
    └── PromptNode.tsx    # Prompt 节点（33 行）

stores/
├── flowStore.ts          # 画布节点/边状态（142 行）
├── canvasStore.ts        # Endless Canvas replay/层级状态
├── layer2Store.ts        # Layer2 子面板状态
├── agentStore.ts         # Agent 列表状态（53 行）
├── memoryStore.ts         # 记忆状态（80 行）
├── debugStore.ts         # SSE 事件/执行历史 + runtime observation
└── uiStore.ts            # UI 状态（15 行）

lib/
├── api.ts                 # API 客户端（JWT 刷新 + SSE）
├── auth.ts               # 认证（119 行）
├── sse-dispatch.ts        # SSE 事件分发（dispatchSSEEvent 三入口）
├── utils.ts              # 通用工具
└── canvas/
    └── wsClient.ts        # Endless Canvas WebSocket 客户端
```

### 12.3 Zustand Stores

**flowStore**（核心画布状态）:
```typescript
interface FlowState {
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  // 方法
  onNodesChange: OnNodesChange        // React Flow 内置
  onEdgesChange: OnEdgesChange        // React Flow 内置
  onConnect: OnConnect                // React Flow 内置
  addNode: (node: Node, addAgent?)    // 添加节点（Agent 自动调 API）
  updateNodeData: (id, data)          // 更新节点数据
  // 持久化: localStorage + 500ms debounce
}
```

### 12.4 API 客户端（api.ts, 440 行）

| 功能 | 说明 |
|------|------|
| **Agent CRUD** | `getAgents()`, `createAgent()`, `deleteAgent()` |
| **SSE 执行** | `executeWithSSE()` — 自动 401 刷新 token |
| **Memory** | `getMemories()`, `getMemoryLayers()`, `storeMemory()` |
| **Communication** | `getMessages()`, `sendMessage()` |
| **KG** | `searchEntities()`, `expandEntity()` |

**JWT 自动刷新**: 检测 401 → refresh → 重试（防止并发刷新）

### 12.5 页面路由

```
/              → 单 agent 对话页（Home 组件，自动取 agents[0]，executeWithSSE→POST /v1/execute）
/login         → 登录页
/canvas        → React Flow 画布
/canvas/live   → 实时画布页（WebSocket，TickCanvas/BranchManager/Layer2Panel）
/agents        → Agent 管理列表
/flows         → Flow 列表（Phase 5.2 新增）
/memory        → 4-tab 面板（记忆 MemoryPanel / 通信 CommunicationPanel / 调试 DebugPanel / 编排 OrchestrationPanel，默认 memory tab）
```

---

## 13. Phase 0-9 实施记录

### 13.1 完整 Git 提交历史

| Commit | 阶段 | 说明 |
|--------|------|------|
| `39b9f07` | Phase 0 | 初始化 Agent OS 项目脚手架 |
| `8ff91ab` | Phase 1-4 | 骨架完成 + Docker 修复 |
| `31c6967` | Phase 5-6 | 前端画布 MVP + 集成联调 |
| `499d1d6` | Phase 7 | 记忆增强 V1（向量 + 压缩 + 多层） |
| `7498362` | Phase 8-9 | 多 Agent 协作 + 生产化 V2 |
| `9e12f38` | 修复 | HTTP 状态码、ContextManager、SSE 重试 |
| `1b2edcd` | 增强 | 默认初始化、自动创建 Agent、聊天 UI |
| `b13e87d` | SSE | 执行端点 SSE 流 + Gateway 代理 + 前端消费 |
| `c497ef5` | Phase 7 | 向量搜索、双触发压缩、4 层迁移 |
| `786c784` | 修复 | reasoning-chain 检测 + 压缩分组逻辑 |
| `0101578` | 修复 | Episodic→Semantic 迁移接入每日 sweep |
| `7c55eb4` | UI | 导航统一、/flows 页面、Agent 面板 |
| `8a811fd` | UI | Canvas localStorage 持久化（500ms debounce） |
| `e3d7b06` | 安全 | JWT 安全加固（RS256 + 旋转 + Rate Limiting） |
| `92cf644` | 修复 | JWT 审查 — 3 个重要修复 |
| `b09cceb` | 修复 | 压缩引擎空转 bug（11→11→0 summaries 修复） |
| `011b893` | Phase 9 | 轻量 SQLite 持久化实现 |
| `38764ee` | 文档 | D-11/D-12 架构文档同步（SQLite KG） |
| `31b325d` | Gap-Fill | Safety deadline turns + reflect() + 文档更新 |
| `926ce71` | 重构 | engine.py 拆分为 api/routes + services |
| `93eaf06` | 重构 | MemoryService 拆分为专注子模块 |
| `9cc0a46` | 文档 | D-24 L3.3 Implementations 设计 |
| `b66abe4` | 文档 | D-25 三层记忆架构 + Sideline Memory Agent |
| `9ea5460` | 文档 | D-26 JWT 安全 + D-27 压缩引擎修复 |
| `09dad8d` | L3.3 | Primitive 工具实现 |
| `ce0f682` | L3.3 | Skill 工具实现（browser/code/memory） |
| `196397d` | L3.3 | Composite 工具实现 |
| `5891c48` | L3.4 | Catalog 实现 |
| `4681b38` | 重构 | 分离 skills/ 内容目录和 skill_catalog/ 管理器 |
| `2a966a1` | 模板 | .agent-os-template 用户扩展目录 |
| `539adba` | Phase 8 | Sideline Memory Agent 实现 |
| `47d0824` | 测试 | L3.3/L3.4 集成测试 |
| `a1b3b9b` | 修复 | P0/P1/P2/P3 代码审查修复 |
| `5661b60` | 修复 | P0 问题修复 |
| `ee9c58d` | 修复 | P1 问题修复 |
| `efb2e8a` | 修复 | P2 问题修复 |
| `21992a6` | 修复 | P3 修复 + Baseline 增强 |
| `77f4aed` | 修复 | 浏览器工具替换为真实 Playwright |
| `c0dcb89` | 修复 | 消除 browser_flow 同步/异步重复代码 |
| `a0e94e2` | 修复 | 添加缺失的 file_write/file_delete 导入 |
| `419722a` | 修复 | memory_store 通过 MemoryService/SQLiteStore 持久化 |
| `72aeece` | 修复 | 添加 pysqlite3 fallback（vector.py + db_tool.py） |
| `ef15806` | 修复 | 添加 lru_cache 单例到 _get_memory_service() |
| `17fce74` | 修复 | 添加 pysqlite3 fallback（rag_engine.py） |
| `2e85218` | D-26 P0 | SkillLoader + SkillCatalog |
| `6a20104` | D-26 P1 | SkillExecutor + SkillConfig |
| `bd88cb4` | D-26 P2 | ToolRegistry Bridge + Prompt 层集成 |
| `31cdc97` | D-26 P3 | HotReload + CLI 工具 |
| `36ed514` | D-26 Fix | C-1/C-2 SkillCatalog 修复 |
| `1275141` | D-26 Fix | H-1 dead code 清理 |
| `fb44637` | D-26 Fix | H-2 register_skill visible 检查 |
| `3987fab` | D-26 Fix | H-3 polling loop try/except |
| `fa6af9a` | 文档 | D-28 User Skill Plug-in 添加到架构 |

### 13.2 Phase 验收状态

| Phase | 状态 | 审计评分 | 备注 |
|-------|------|---------|------|
| Phase 0 | ✅ 完成 | — | 开发环境 + Docker |
| Phase 1 | ✅ 完成 | — | 图状态机 + Checkpoint |
| Phase 2 | ✅ 完成 | — | 记忆 MVP（InMemoryStore） |
| Phase 3 | ✅ 完成 | — | ContextCompiler + ContextManager |
| Phase 4 | ✅ 完成 | — | ToolExecutor + Guardrail |
| Phase 5 | ✅ 完成 | — | React Flow 画布 MVP |
| Phase 6 | ✅ 完成 | — | 全链路 SSE 集成 |
| Phase 7 | ✅ 完成 | — | 向量 + 压缩 + 多层迁移 |
| Phase 8 | ✅ 完成 | — | CommunicationBus + Concurrency |
| Phase 9 | ✅ 完成 | — | SQLite 持久化 + 轻量 KG |

**总审计评分**: 9.5/10，97% 完成度

### 13.3 遗留项（不阻塞）

1. 无界数据结构（长期运行内存增长）
2. 无 API 版本控制（/v1/ 前缀）
3. `_revoked_jtis` 内存存储（需 Redis 持久化）
4. 自动生成 RSA 密钥未持久化到磁盘
5. 前端 Agent 节点面板（V2 计划）
6. 前端事件历史时间线（V2 计划）

---

## 14. 关键设计决策

### D-01: 编排层自研定位 ✅
- **决策**: 在 LangGraph 层级自研，不用 pydantic-ai（太高）也不用 LangChain（太低）
- **依据**: 7 框架对比研究

### D-02: 五大自研模块 ✅
- ContextCompiler, MemoryService, ToolExecutor, CommunicationBus, ConcurrencyController

### D-03: 记忆四层模型 ✅
- Working → Session → Episodic → Semantic

### D-04: 双触发压缩机制 ✅
- 70% 饱和度: 异步，不阻塞
- 85% 饱和度: 同步，2s 超时

### D-05: 信任域隔离 ✅
- Workspace / Session / Agent / Global 四级

### D-06: ContextCompiler vs MemoryService 边界 ✅
- MemoryService: 知道什么（CRUD/recall/compress/forget）
- ContextCompiler: 组装什么（compile → 调用 MemoryService → 输出上下文）

### D-07: 层间迁移规则 ✅
- Working→Session: 同步
- Session→Episodic: 异步 checkpoint
- Episodic→Semantic: 定时（Heartbeat/Cron）

### D-08: 重要性评分 ✅
- 五维度权重 + 可配置模板（coding/research/general）

### D-09: 工具结果生命周期 ✅
- 安全期轮次计数器（默认 4 轮 sweep）
- result(可丢) + reasoning_context(永久)

### D-10: 前端记忆可视化 ✅
- 独立 Memory 面板（四层全可见）
- 调试模式（开关显示压缩/遗忘事件流）

### D-11: KG 层选型 ✅（2026-04-06 更新）
- 轻量 SQLite KG → 预留 Neo4j 迁移路径

### D-12: 存储介质演进 ✅（2026-04-06 更新）
- MVP: InMemory + FAISS 内存
- 当前: SQLiteStore + FAISS 文件 + SQLite KG
- V2: PostgreSQL + pgvector + Neo4j

---

## 15. 文件结构索引

### 后端核心

```
services/orchestrator/src/
├── engine.py                    # FastAPI 入口（571 行）
├── api/
│   ├── models.py               # Pydantic 模型（52 行）
│   └── routes/
│       ├── agents.py           # Agent CRUD（58 行）
│       ├── chat.py             # SSE 对话流（1031 行）
│       ├── memory.py           # 记忆 API（851 行）
│       ├── entities.py         # KG 实体（84 行）
│       ├── orchestrate.py      # 多 Agent 编排（231 行）
│       ├── canvas.py           # 无尽画布 / replay（330 行）
│       └── pitfail.py          # PitFail 端点（80 行）
├── services/
│   ├── agent_manager.py        # Agent 生命周期（64 行）
│   ├── llm_client.py           # LLM 客户端（57 行）
│   └── _state.py               # 全局状态（102 行）
├── graph/
│   ├── state.py                # GraphState（61 行）
│   ├── nodes.py                # 节点类型（86 行）
│   ├── edges.py                # 边路由（70 行）
│   └── __init__.py             # StateGraph（480 行）
├── context/
│   ├── compiler.py             # ContextCompiler（111 行）
│   └── manager.py              # ContextManager（158 行）
├── memory/
│   ├── service.py              # MemoryService 门面（346 行）
│   ├── store.py                # InMemoryStore（298 行）
│   ├── sqlitestore.py          # SQLiteStore（505 行）
│   ├── pgstore.py              # PostgresStore（565 行）
│   ├── types.py                # 核心类型（194 行）
│   ├── compressor.py           # 压缩引擎（511 行）
│   ├── forgetting.py           # 主动遗忘（236 行）
│   ├── migrator.py             # 层间迁移（376 行）
│   ├── scorer.py               # 重要性评分（245 行）
│   ├── knowledge_graph.py      # SQLite KG（849 行）
│   ├── embedding.py            # SentenceTransformer（73 行）
│   ├── vector.py              # FAISS 向量（337 行）
│   ├── permissions.py          # 权限管理（345 行）
│   ├── _blocks.py              # Block 操作（40 行）
│   ├── _crud.py                # CRUD 操作（137 行）
│   ├── _session.py             # Session 操作（130 行）
│   ├── _recall/
│   │   ├── base.py             # ABC（25 行）
│   │   ├── keyword_recall.py   #（45 行）
│   │   ├── semantic_recall.py  #（62 行）
│   │   ├── kg_recall.py        #（61 行）
│   │   └── shared_recall.py    #（55 行）
│   └── sideline/
│       ├── agent.py            # 旁路 Agent（196 行）
│       ├── baseline.py         #（174 行）
│       ├── rag_engine.py       #（253 行）
│       └── verifier.py         #（161 行）
├── tools/
│   ├── executor.py             # 工具执行器（125 行）
│   ├── registry.py             # 工具注册表（35 行）
│   └── guardrail.py            # 安全检查（89 行）
├── communication/
│   ├── bus.py                  # 通信总线（425 行）
│   ├── message.py              # 消息类型（152 行）
│   └── scope.py                # 作用域管理（227 行）
├── concurrency/
│   └── controller.py           # 并发控制器（407 行）
├── skills/
│   ├── skill_loader.py         # Skill 加载器（243 行）
│   ├── skill_executor.py       # Skill 执行器（230 行）
│   ├── skill_config.py         # Skill 配置（243 行）
│   ├── skill_catalog.py        # Skill 目录（143 行）
│   ├── prompt_integration.py   # Prompt 注入（128 行）
│   ├── tool_registry_integration.py  #（293 行）
│   ├── hot_reload.py           # 热重载（388 行）
│   └── cli.py                  # CLI 工具（401 行）
└── skill_catalog/
    ├── loader.py               # 文件扫描（227 行）
    ├── registry.py             # 元数据注册（181 行）
    └── config.py               # 配置管理（177 行）

services/gateway/src/
├── main.py                    # FastAPI 入口（112 行）
├── auth.py                    # JWT 认证（161 行）
├── config.py                  # 配置（29 行）
├── middleware.py              # 中间件（157 行）
└── routes/
    ├── agents.py, auth.py, chat.py, conversations.py
    ├── debug.py, execute.py, kg.py, memories.py
    ├── messages.py, prompts.py, resources.py
```

### 前端

```
apps/web/src/
├── app/
│   ├── layout.tsx             # 根布局
│   ├── page.tsx               # 根页面（/）
│   ├── canvas/page.tsx        # 画布页面
│   ├── agents/page.tsx        # Agent 页面
│   ├── flows/page.tsx         # Flow 列表
│   ├── memory/page.tsx        # Memory 面板
│   └── login/page.tsx         # 登录页
├── components/
│   ├── canvas/
│   │   ├── FlowCanvas.tsx     # React Flow 主画布（154 行）
│   │   ├── PropertyPanel.tsx  # 属性面板（278 行）
│   │   ├── ExecutePanel.tsx   # 执行面板（109 行）
│   │   ├── edges/DataEdge.tsx # 数据边
│   │   └── nodes/
│   │       ├── AgentNode.tsx  # Agent 节点（39 行）
│   │       ├── ToolNode.tsx   # Tool 节点（28 行）
│   │       └── PromptNode.tsx  # Prompt 节点（33 行）
│   ├── layout/
│   │   ├── Header.tsx         # 导航头
│   │   └── Sidebar.tsx        # 侧边栏
│   └── panels/
│       ├── CommunicationPanel.tsx
│       ├── DebugPanel.tsx
│       ├── ExecutionHistoryPanel.tsx
│       ├── MemoryPanel.tsx
│       └── OrchestrationPanel.tsx
├── stores/
│   ├── flowStore.ts           # 画布状态（142 行）
│   ├── agentStore.ts          # Agent 状态（53 行）
│   ├── memoryStore.ts         # 记忆状态（80 行）
│   ├── debugStore.ts          # SSE 事件（90 行）
│   └── uiStore.ts             # UI 状态（15 行）
├── lib/
│   ├── api.ts                 # API 客户端（440 行）
│   └── auth.ts                # 认证（119 行）
└── types/
    ├── agent.ts
    └── flow.ts
```

---

*文档生成: 2026-04-17*
*项目专家-00 — Agent OS 实施指南 v1.0*
