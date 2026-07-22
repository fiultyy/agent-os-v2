# Workflow Kernel Design (W 全集 P0+P1+P2)

> 综合自 3 份 DesignDoc + 3 份 JudgeVerdict。骨架取 judge 共识 winner(单文件
> `workflow_engine.py` + 单一 `_spawn_agent` chokepoint,design [1]),grafting 各 judge
> 标注的最佳想法:
> - **[2] grep 机械守恒红线**(R1/R2 写成可执行的 grep 命令)。
> - **[2] `workflow_event` UNIQUE INDEX `(run_id, key, type)`** 强制 cache 命中唯一性。
> - **[0] `WorkflowContext` dataclass 作 P1/P2 增量接入载体** — 字段命名公开化(`shared_usage`
>   非下划线,修正 [0]/[2] 的封装泄露 code smell)。
> - **[1] schema registry 纯 dict + `resolve_schema`**(复用 pydantic-ai `Agent(output_type=...)`)。
> - **[0] `v2_workflow.py` 独立工具薄桥文件**(工具暴露层与内核正交,P1 加 loop 只加一行清单)。
> - **[2] WorktreeManager 不踩 ADR-4 的显式论证**(native in-process Agent 无 harness client)。
>
> 修正 judge 标出的硬伤:
> - **`v2_` 前缀双重叠加**:`ToolBridgeCapability.get_toolset` 已 `.prefixed("v2")`
>   (tool_bridge_capability.py:83 verified),所以 register 名必须用 `workflow_run` /
>   `workflow_loop`(无 v2_ 前缀),ToolBridge 自动加成 `v2_workflow_run`。
> - **worktree cwd 透传**:`build_native_agent` 签名(verified)无 `cwd`/`deps` 参数,
>   `deps={'cwd':cwd}` 不可行([2] sketch)。P2 改为 `os.chdir` 上下文管理器
>   + per-agent 独占执行(并发 fan-out 用 `asyncio.Semaphore` 串行化 worktree node)。
> - **pipeline 闭包 bug**([2]):`for stage in stages: async def _stage_worker()`
>   捕获循环变量 — 改为 `default=stage` 参数绑定。
> - **dry counter 阈值不统一**:三 design 各执 2 vs 3,统一 `dry>=2` 早收敛省 budget。
> - **`flow_event` 命名空间碰撞**:workflow 新增值前缀化为 `workflow_*` /
>   `workflow_node_*` 避免与 flow.py 的 `node_completed` 同字符串碰撞。
> - **并发 RunUsage 写竞态**:P0 即用 per-node RunUsage 后 fan-in 聚合(非共享可变引用)。
> - **emit `node_failed` 语义**:observe EventType enum 冻结,失败也 wire 成
>   `tick_completed` + `data.flow_event='workflow_node_completed'`,`status='failed'`
>   塞 `data.flow_payload`。

---

## 1. 总览

`workflow_engine` 是 agent-os-v2 的"workflow 内核":为 native in-process Agent
(`build_native_agent`)提供 fan-out / fan-in / 阶段链 / budget / loop / 嵌套 / worktree
/ 跨进程 resume 原语。所有 spawn 经 `build_native_agent`(ADR-sanctioned 统一入口,
非 R2 禁项),所有 fan-in 在内核单点完成(`R1`:子 agent 零 memory 落库)。

### 1.1 模块图

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    主路径 /h/agent-os-v2                  │
                    │  native Agent ── ToolBridgeCapability ── workflow_run()  │
                    │                                  workflow_loop()          │
                    └───────────────────────┬─────────────────────────────────┘
                                            │ (ToolExecutor.execute 包装)
              ┌─────────────────────────────▼──────────────────────────────────┐
              │                tools/composite/v2_workflow.py                  │
              │  register: workflow_run / workflow_loop (无 v2_ 前缀!)         │
              │  schema(给模型提示) + handler(薄桥调 workflow_engine.*)     │
              └─────────────────────────────┬──────────────────────────────────┘
                                            │
              ┌─────────────────────────────▼──────────────────────────────────┐
              │                workflow_engine.py(P0+P1 单文件)                 │
              │                                                                │
              │  WorkflowEngine 类(emitter / pitfail / tool_executor 注入)    │
              │     │                                                          │
              │     ├── _spawn_agent()   ← 单一 chokepoint(R1/R2/空串/降级) │
              │     │       └── build_native_agent()                          │
              │     │       └── capabilities=[ToolBridgeCapability,           │
              │     │                       ObserveCapability?]                │
              │     │       └── task_input = prompt or "(no task input)"      │
              │     │       └── try/except → 降级 dict(ADR-7)                │
              │     │                                                          │
              │     ├── run()           ← P0 fan-out + fan-in(单点)         │
              │     │       └── asyncio.gather(return_exceptions=True)        │
              │     │       └── asyncio.Semaphore(concurrency)                │
              │     │       └── per-node RunUsage → fan-in 聚合(无并发竞态)│
              │     │       └── _emit_workflow(wire tick_completed)           │
              │     │                                                          │
              │     ├── pipeline()     ← P1 无 barrier 阶段链(Queue 串联)    │
              │     ├── loop()         ← P1 while + seen set + dry counter    │
              │     │       + budget.total guard                               │
              │     │                                                          │
              │     └── _emit_workflow()  ← fire-and-forget(ADR-7)           │
              │             data.flow_event = workflow_* / workflow_node_*    │
              │                                                                │
              │  WorkflowContext dataclass  ← P0/P1/P2 增量接入载体            │
              │  _SCHEMA_REGISTRY dict       ← schema_ref → pydantic BaseModel │
              └─────────────────────────────┬──────────────────────────────────┘
                                            │ (P2 拆出独立子模块)
              ┌─────────────────────────────▼──────────────────────────────────┐
              │   workflow_engine/  P2 子模块(defer,不提前创建文件)         │
              │     ├── nesting.py      ContextVar 一层限制                    │
              │     ├── worktree.py     WorktreeManager(opt-in)               │
              │     └── journal.py      workflow_run + workflow_event SQLite   │
              └────────────────────────────────────────────────────────────────┘
```

### 1.2 P0/P1/P2 切分原则

- **P0**:`run`(fan-out + fan-in)+ `_spawn_agent`(红线 chokepoint)+ 工具暴露层
  + schema registry。单文件 `workflow_engine.py` + `tools/composite/v2_workflow.py`
  + `engine.py` 加 1 行 `_WORKFLOW_TOOLS` 清单。可独立 ship。
- **P1**:`pipeline`(无 barrier 阶段链)+ `loop`(控制流)+ budget 强制
  (`UsageLimits.check`)。在同一 `workflow_engine.py` 内追加方法,不改 P0 代码。
- **P2**:嵌套 / worktree / 跨进程 resume。拆 `nesting.py` / `worktree.py` /
  `journal.py` 子模块(此时 P2 才创建文件,P0/P1 不创建)。

---

## 2. 接口签名表

### 2.1 内核原语(`workflow_engine.py`)

| 名称 | 签名 | 层 |
|---|---|---|
| `WorkflowEngine.__init__` | `def __init__(self, emitter: ObserveEmitter \| None, pitfail_registry: PitfallRegistry \| None, tool_executor: ToolExecutor \| None) -> None` | P0 |
| `WorkflowEngine.run` | `async def run(self, spec: WorkflowNodesSpec, ctx: WorkflowContext) -> WorkflowResult` | P0 |
| `WorkflowEngine._spawn_agent` | `async def _spawn_agent(self, node: WorkflowNodeSpec, ctx: WorkflowContext) -> NodeResult` | P0 |
| `WorkflowEngine.pipeline` | `async def pipeline(self, spec: PipelineSpec, ctx: WorkflowContext) -> list[Any]` | P1 |
| `WorkflowEngine.loop` | `async def loop(self, spec: LoopSpec, ctx: WorkflowContext) -> WorkflowResult` | P1 |
| `WorkflowEngine._emit_workflow` | `def _emit_workflow(self, event_name: str, run_id: str, payload: dict, session_id: str \| None = None) -> None` | P0 |
| `WorkflowContext` | `@dataclass class WorkflowContext: session_id: str; agent_id_prefix: str; run_id: str; concurrency: int = 8; budget_limits: UsageLimits \| None = None; total_usage: RunUsage = field(default_factory=RunUsage); seen: set[str] = field(default_factory=set); dry_counter: int = 0; depth: int = 0; abort: asyncio.Event \| None = None; journal: "WorkflowJournal \| None" = None; worktree_manager: "WorktreeManager \| None" = None` | P0(P1/P2 字段先 None) |
| `WorkflowNodesSpec` | `class WorkflowNodesSpec(BaseModel): nodes: list[WorkflowNodeSpec] = Field(min_length=1, max_length=4096); fan_in: Literal["list","merge"] = "list"; timeout_per_node_ms: int = 120000` | P0 |
| `WorkflowNodeSpec` | `class WorkflowNodeSpec(BaseModel): prompt: str = Field(min_length=1); label: str = "node"; model: str \| None = None; schema_ref: str \| None = None; effort: Literal["low","medium","high","xhigh","max"] = "medium"; isolation: Literal[None,"worktree"] = None` | P0 |
| `NodeResult` | `@dataclass class NodeResult: label: str; agent_id: str; output: Any = None; usage: RunUsage = field(default_factory=RunUsage); status: Literal["success","error","timeout"] = "success"; error: str \| None = None` | P0 |
| `WorkflowResult` | `@dataclass class WorkflowResult: status: Literal["success","error"]; node_results: list[NodeResult]; total_usage: RunUsage; elapsed_ms: int; node_count: int; run_id: str` | P0 |
| `resolve_schema` | `def resolve_schema(ref: str \| None) -> type[BaseModel] \| None` | P0 |
| `register_schema` | `def register_schema(name: str, model: type[BaseModel]) -> None` | P0(扩展点,默认空) |

### 2.2 工具 handler(`tools/composite/v2_workflow.py`)

> **命名约束**(judge cross-design risk):`ToolBridgeCapability.get_toolset`
> 已 `.prefixed("v2")`(tool_bridge_capability.py:83 verified),所以 register
> 名必须用 `workflow_run` / `workflow_loop`(**无 v2_ 前缀**),ToolBridge 自动
> 加成 `v2_workflow_run`。若 register 时带 v2_,模型实际看到 `v2_v2_workflow_run`。

#### 2.2.1 `workflow_run` 工具 JSON Schema

```json
{
  "type": "object",
  "required": ["nodes"],
  "additionalProperties": false,
  "properties": {
    "nodes": {
      "type": "array",
      "minItems": 1,
      "maxItems": 4096,
      "items": {
        "type": "object",
        "required": ["prompt"],
        "additionalProperties": false,
        "properties": {
          "prompt":        {"type": "string", "minLength": 1},
          "label":         {"type": "string"},
          "model":         {"type": "string"},
          "schema_ref":    {"type": "string"},
          "effort":        {"type": "string", "enum": ["low","medium","high","xhigh","max"]},
          "isolation":     {"type": "string", "enum": ["worktree"]}
        }
      }
    },
    "fan_in":             {"type": "string", "enum": ["list","merge"], "default": "list"},
    "timeout_per_node_ms":{"type": "integer", "minimum": 1000, "default": 120000},
    "concurrency":        {"type": "integer", "minimum": 1, "maximum": 64, "default": 8}
  }
}
```

#### 2.2.2 `workflow_loop` 工具 JSON Schema(P1)

```json
{
  "type": "object",
  "required": ["finder_spec"],
  "additionalProperties": false,
  "properties": {
    "finder_spec":  {"$ref": "#/node"},  // single WorkflowNodeSpec(产候选的 finder)
    "max_iter":     {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    "budget": {
      "type": "object",
      "properties": {
        "request_limit":       {"type": "integer"},
        "input_tokens_limit":  {"type": "integer"},
        "output_tokens_limit": {"type": "integer"},
        "total_tokens_limit":  {"type": "integer"}
      }
    },
    "schema_ref":   {"type": "string"},
    "seen_key_fn":  {"type": "string", "enum": ["content_hash","label"], "default": "content_hash"},
    "dry_limit":    {"type": "integer", "minimum": 1, "maximum": 5, "default": 2}
  }
}
```

#### 2.2.3 handler 签名

```python
# tools/composite/v2_workflow.py
async def workflow_run_handler(
    nodes: list[dict],
    fan_in: str = "list",
    timeout_per_node_ms: int = 120000,
    concurrency: int = 8,
) -> dict[str, Any]:
    """P0 入口。handler 内首行 WorkflowNodesSpec.model_validate(...) 二次校验
    (防 any_schema 直注绕过),非法 → {'status':'error','error':'invalid nodes spec'}。
    薄桥调 WorkflowEngine.run(spec, ctx)。返回经 ToolExecutor 状态化为
    {'status':'success'|'error','output': {...WorkflowResult dict...}}。"""

async def workflow_loop_handler(
    finder_spec: dict,
    max_iter: int = 10,
    budget: dict | None = None,
    schema_ref: str | None = None,
    seen_key_fn: str = "content_hash",
    dry_limit: int = 2,
) -> dict[str, Any]:
    """P1 入口。薄桥调 WorkflowEngine.loop(spec, ctx)。"""
```

### 2.3 P2 子模块接口

```python
# workflow_engine/nesting.py
async def nested_run(
    spec: WorkflowNodesSpec,
    parent_ctx: WorkflowContext,
) -> WorkflowResult:
    """一层限制:parent_ctx.depth >= 1 → raise WorkflowNestingError('one-level only')。
    构造 child_ctx = replace(parent_ctx, depth=parent_ctx.depth+1,共享
    total_usage(同 RunUsage 引用)/ concurrency(同 Semaphore)/ abort(同 Event)/
    worktree_manager / journal),_WF_CTX.set(child_ctx) 后调 WorkflowEngine.run,
    finally _WF_CTX.reset(token)。"""

# workflow_engine/worktree.py
class WorktreeManager:
    def __init__(self, base: Path) -> None: ...
    async def acquire(self, agent_id: str, run_id: str, base_ref: str = "HEAD") -> Path: ...
    async def release(self, agent_id: str, keep: bool = False) -> None: ...
    async def __aenter__(self) -> "WorktreeManager": ...
    async def __aexit__(self, *exc) -> None: ...  # 释放所有未 release 的 worktree

# workflow_engine/journal.py
class WorkflowJournal:
    def __init__(self, db_path: Path) -> None: ...
    def start_run(self, run_id: str, spec_json: str, session_id: str,
                  parent_run_id: str | None = None) -> None: ...
    def append_event(self, run_id: str, ev: WorkflowEvent) -> None: ...
    def list_events(self, run_id: str) -> list[WorkflowEvent]: ...
    def mark_completed(self, run_id: str, status: str, total_usage: RunUsage) -> None: ...
    async def resume(self, run_id: str, engine: "WorkflowEngine") -> dict[str, Any]: ...
```

---

## 3. 红线守护矩阵(7 条 × guard 点 × file:line)

| # | 红线 | guard 点 | 落地形式(机械可验) | file:line / file |
|---|---|---|---|---|
| R1 | 子 agent 零 `MemoryWriterCapability`;fan-in 单点落库;`workflow_engine` 模块 grep 零 memory 调用 | `_spawn_agent` caps 组装 + CI grep | caps 列表只 `[ToolBridgeCapability, ObserveCapability?]`,**绝不 import/append `MemoryWriterCapability`**;CI grep:`grep -rE 'memory_event_bus\|_trigger_ingest\|_trigger_kg_extraction\|memory_service' src/harness/workflow_engine*.py src/harness/workflow_engine/ src/tools/composite/v2_workflow.py` 必须空 | `workflow_engine.py:_spawn_agent`(对位 `agent_runner.py:90-106` + `:106` R1 注释) |
| R2 | 不 import/改 `_build_execution_graph`/`_node_llm`/`chat.py` /`execute` 线性图/`routes.py trigger_turn`;workflow 经 `build_native_agent` 合法 | `workflow_engine.py` 顶部 import 白名单 + CI grep | import 白名单:`asyncio` / `contextvars` / `dataclasses` / `logging` / `pydantic` / `pydantic_ai` / `build_native_agent` / `ObserveCapability` / `ToolBridgeCapability` / `flow_event` wire / `_state`(仅 `tool_executor`+`pitfail_registry`+`memory_observe_emitter`)。CI grep:`grep -rE '_build_execution_graph\|_node_llm\|from.*chat import\|trigger_turn\|_build_multi_agent_graph\|/v1/orchestrate' src/harness/workflow_engine*.py src/harness/workflow_engine/` 必须空 | `workflow_engine.py` import 区(对位 `native_agent.py:80` docstring + `agent_runner.py:22` R2 注释) |
| R3 | ADR-7:`emit` fire-and-forget;workflow 异常不得 abort 主路径 | `_emit_workflow` 内层 try/except + `_spawn_agent` 外层 try/except + `run` 用 `gather(return_exceptions=True)` | (a) `_emit_workflow` 整体包 `try/except Exception: logger.warning(...)`(对位 `emit.py:81-105` + `flow.py:453-459`);(b) `_spawn_agent` 的 `agent.run(...)` 外层 `try/except Exception` → 降级返 `NodeResult(status='error', error=str(exc))`(对位 `agent_runner.py:118-122`);(c) `run` 的 `gather(..., return_exceptions=True)`,显式 `isinstance(r, Exception)` 转 `NodeResult(status='error')` | `workflow_engine.py:_emit_workflow` / `_spawn_agent` / `run` |
| R4 | observe `EventType` enum 冻结;workflow 事件 wire 成 `tick_completed`,真实语义塞 `data.flow_event` + `data.flow_payload`;禁改 enum | `_emit_workflow` 照搬 `flow_event()` 构造 | 固定 `event_type='tick_completed'`,`data.flow_event` 取自冻结枚举集(见下),"真实 payload" 塞 `data.flow_payload`。**绝不 import `EventType` 或新增枚举值** | `workflow_engine.py:_emit_workflow`(对位 `flow.py:98-118`) |
| R5 | `EngineeringDisciplineCapability` 默认 prepend 不可绕过(`native_agent.py:114`) | `_spawn_agent` 调 `build_native_agent` 不传 `EngineeringDisciplineCapability` 实例 | 调 `build_native_agent(capabilities=[ToolBridgeCapability,...])` 时**不实例化 `EngineeringDisciplineCapability`** → 走 `native_agent.py:114-116` 默认 prepend 分支。`workflow_engine.py` 禁止 import `EngineeringDisciplineCapability`(CI grep `grep -rE 'EngineeringDisciplineCapability' src/harness/workflow_engine*.py` 必须空) | `workflow_engine.py:_spawn_agent`(对位 `native_agent.py:113-116`) |
| R6 | 空串占位:子 agent input 非空,空加 `(no task input)`(智谱/Anthropic 通道 400 code 1214) | `_spawn_agent` 一行 guard + handler `WorkflowNodesSpec` pydantic 二次校验 | (a) `task_input = node.prompt or "(no task input)"`(逐字照搬 `agent_runner.py:116`);(b) `WorkflowNodeSpec.prompt: str = Field(min_length=1)` 入口兜底 | `workflow_engine.py:_spawn_agent`(对位 `agent_runner.py:116`) + `v2_workflow.py:workflow_run_handler` |
| R7 | pitfall 语义鸿沟:`tool_executor.execute` 返 `{status:'error'}` 是返回值非 raise;`on_tool_execute_error` 不触发;wrapper 显式转 | `run` 的 `gather` 后处理 + `ToolBridgeCapability._record_pitfall` 复用 | `results = await asyncio.gather(*tasks, return_exceptions=True)`,`for r in results: if isinstance(r, Exception): → NodeResult(status='error', error=str(r))`。子 agent 工具失败自动落 pitfall(经 `ToolBridgeCapability._record_pitfall`,workflow 不重复计数) | `workflow_engine.py:run`(对位 `tool_bridge_capability.py:15-19` P7 注释 + `:86-106`) |

### 3.1 `data.flow_event` 冻结值集合

> flow.py 现有 `flow_started` / `node_completed` 等,workflow 新增值**全部前缀化**
> `workflow_*` / `workflow_node_*` 避免与 flow 的 `node_completed` 同字符串碰撞
> (judge cross-design risk)。

```python
WORKFLOW_FLOW_EVENTS: frozenset[str] = frozenset({
    "workflow_started",         # run 级
    "workflow_completed",       # run 级(status + total_usage)
    "workflow_node_started",    # per-agent(agent_id + label + model)
    "workflow_node_completed",  # per-agent(status + output + usage;含 status='failed')
    "workflow_pipeline_stage_started",   # P1
    "workflow_pipeline_stage_completed", # P1
    "workflow_loop_started",            # P1(iter=0)
    "workflow_loop_iteration",          # P1(iter + new_count + dry_count + seen_size)
    "workflow_loop_completed",          # P1(total_candidates + dry_streak)
})
```

TUI / observe 消费端按 `data.flow_event` 过滤;P0 落地即加 TUI 端 `workflow_*`
分支显式断言(judge cross-design risk mitigation)。

---

## 4. 复用清单(asset × file:line × 用途)

| asset | file:line | 用途 |
|---|---|---|
| `build_native_agent` | `services/orchestrator/src/harness/native_agent.py:63-128` | `_spawn_agent` 唯一 spawn 入口(R2 合法,ADR-sanctioned)。零改动。提供 `EngineeringDisciplineCapability` 默认 prepend(:114-116,R5)+ `model_name` 覆盖 + `AnthropicModel` 智谱 glm 通道 |
| `run_agent_turn` 模式 | `services/orchestrator/src/agent/meta/agent_runner.py:42-124` | `_spawn_agent` 母版:caps 组装无 `MemoryWriterCapability`(:90-106 R1)+ 空串占位(:116 R6)+ Agent.run try/except 降级(:118-122 R3)+ `ObserveCapability` 可选挂(:99-105) |
| `asyncio.gather(return_exceptions=True)` | stdlib asyncio | `run` 的 BARRIER 原语。`return_exceptions=True` 保证单失败不 abort sibling(匹配 CC `parallel()` "never rejects" 契约)。零自研 |
| `asyncio.Queue` + `asyncio.Semaphore` | stdlib asyncio | P1 pipeline 无 barrier stage 链(Queue 串联)+ 并发 cap(Semaphore,默认 8) |
| `asyncio.Lock` | stdlib asyncio | fan-in 阶段 RunUsage 聚合的并发安全(per-node RunUsage 后 `lock` 内 `+=`,绕开共享可变引用竞态) |
| `pydantic-ai Agent(output_type=...)` | `pydantic_ai.Agent` v2.0 | P0 schema 原语。`StructuredOutput` = `Agent(output_type=resolve_schema(node.schema_ref))`。`model_validate` 兜底 |
| `pydantic-ai RunUsage` + `UsageLimits` | `pydantic_ai.usage` v2.0 | P1 budget 原语。`agent.run(usage=node_usage)` 拿子 agent 用量,fan-in 时 `total_usage += node_usage`(`__add__` 语义)。`UsageLimits.check_before_request` / `check_tokens` 强制点照搬 |
| `contextvars.ContextVar` | stdlib contextvars(Python 3.12) | P2 嵌套:`_WF_CTX: ContextVar[WorkflowContext \| None]`。pydantic-ai `agent/__init__.py:501-525` per-Agent-instance deps 用同模式 |
| `ToolBridgeCapability` + `Tool.from_schema` | `services/orchestrator/src/harness/capabilities/tool_bridge_capability.py:68-150` | 工具自动暴露:`get_toolset`(:68-83)遍历 registry + `.prefixed("v2")`(:83)。`:86-106 _record_pitfall` wrapper 是 R7 来源。`:120-129` 显式失败处理是 R7 转 `{status:'error'}` 模式来源 |
| `ToolExecutor.execute` | `services/orchestrator/src/tools/executor.py:40-123` | `workflow_run_handler` 经 ToolExecutor 包装(timeout + 双向 guardrail + 状态化返回 :115)。返回值是 dict 非 raise(R7 语义鸿沟来源) |
| `engine.py._bulk_register` + `ToolRegistry.register` | `services/orchestrator/src/engine.py:168-178` + `services/orchestrator/src/tools/registry.py:21-49` | 新增 `_WORKFLOW_TOOLS` 清单经 `_bulk_register` 注册(layer=`ToolLayer.COMPOSITE`,`catalog.py:15-19`)。照搬 `_PRIMITIVE_TOOLS` / `_SKILL_TOOLS` 元组清单模式 |
| `OrchSessionStore` SQLite WAL + `busy_timeout` + `pysqlite3` | `services/orchestrator/src/harness/session_store.py:48-86` + `services/orchestrator/src/memory/sqlitestore.py:16-19` | P2 `WorkflowJournal` 照搬:同一 WAL+busy_timeout=5000+synchronous=NORMAL + 复用 `start.py` 的 `pysqlite3` monkey-patch(不另起 patch 路径) |
| `ObserveEmitter.emit` fire-and-forget | `services/orchestrator/src/harness/emit.py:40-114` | `_emit_workflow` 经 `_state.memory_observe_emitter`(若通电)推 `workflow_*` 事件。fire-and-forget R3 已由 emit.py 内置 |
| `flow_event()` wire 模式 | `services/orchestrator/src/harness/flow.py:98-121` | `_emit_workflow` 逐字照搬:固定 `event_type='tick_completed'`,真实语义塞 `data.flow_event` + `data.flow_payload`(R4) |
| `PitfallRegistry` | `services/orchestrator/src/services/_state.py:41` + `tool_bridge_capability.py:86-106` | workflow 子 agent 工具失败自动落 pitfall(经 `ToolBridgeCapability._record_pitfall`,零 workflow 代码)。None-safe |
| `hashlib.sha256` + `json.dumps` | stdlib | P2 journal cache key:`'v2:' + sha256(json.dumps({prompt,opts}, sort_keys=True)).hexdigest()[:16]`。照 CC `journal.jsonl` format |
| `subprocess` git worktree | stdlib subprocess + pathlib | P2 `WorktreeManager.acquire/release`:`git worktree add --detach` / `git worktree remove --force` |

---

## 5. P0/P1/P2 实现顺序 + `implement_node_split`

> 每 node 直接给 implement workflow 切分用:`node_id` / `primitives`(本 node 产出)
> / `files`(改/新建)/ `tier` / `depends_on` / `verify`(单测 + e2e 断言)。

### P0(自洽 ship)

| node_id | primitives | files | tier | depends_on | verify |
|---|---|---|---|---|---|
| **W-P0-1** | `WorkflowContext` dataclass + `WorkflowNodesSpec` / `WorkflowNodeSpec` / `NodeResult` / `WorkflowResult` pydantic/dataclass;`_SCHEMA_REGISTRY` dict + `register_schema` / `resolve_schema`(默认内建 `'text'` passthrough) | `services/orchestrator/src/harness/workflow_engine.py`(新建,~40 行 type 定义段) | P0 | — | 单测:`WorkflowNodesSpec.model_validate({'nodes':[{'prompt':'hi'}]})` 通过;`prompt:''` 拒;`resolve_schema('text')` 返 None(passthrough) |
| **W-P0-2** | `WorkflowEngine._spawn_agent`:caps=[ToolBridgeCapability, ObserveCapability?] + 不挂 MemoryWriterCapability(R1)+ `build_native_agent`(R5 默认 prepend)+ 空串占位(R6)+ try/except 降级返 `NodeResult(status='error')`(R3)+ per-node `RunUsage`(避免共享可变引用竞态) | `services/orchestrator/src/harness/workflow_engine.py`(~50 行) | P0 | W-P0-1 | 单测:`monkeypatch build_native_agent` 返 fake agent,fake `agent.run` raise → `_spawn_agent` 返 `NodeResult(status='error')` 不 raise;成功路径返 `status='success'` + `usage` 填充 |
| **W-P0-3** | `WorkflowEngine.run`:`asyncio.Semaphore(concurrency)` cap + `asyncio.gather(*[_bounded(n)], return_exceptions=True)` + `isinstance(r, Exception)` 转 `NodeResult(status='error')`(R7)+ fan-in 聚合(`fan_in='list'` 原样数组 / `'merge'` dict 字段合并)+ `total_usage` 聚合(per-node RunUsage `+=`,无并发竞态)+ 4 事件 emit(workflow_started / workflow_node_started / workflow_node_completed / workflow_completed) | `services/orchestrator/src/harness/workflow_engine.py`(~80 行) | P0 | W-P0-2 | 单测:mock `build_native_agent` 返 3 fake agent 输出 "a"/"b"/raise → `run` 返 3 个 NodeResult(2 success 1 error),`total_usage` 累加成功 2 个;`fan_in='merge'` 时 dict 字段合并;`fan_in='list'` 原样 |
| **W-P0-4** | `WorkflowEngine._emit_workflow`:`flow_event()` wire 模式逐字照搬(R4);event_type 固定 `'tick_completed'`,真实语义塞 `data.flow_event ∈ WORKFLOW_FLOW_EVENTS` + `data.flow_payload`;emitter=None 跳过;整体 try/except pass(R3) | `services/orchestrator/src/harness/workflow_engine.py`(~25 行) | P0 | W-P0-3 | 单测:`emitter=None` 不崩;emit 招回抛异常不冒泡;构造事件 dict 含 `data.flow_event='workflow_started'` |
| **W-P0-5** | `tools/composite/v2_workflow.py`:`workflow_run_handler`(薄桥 — handler 首行 `WorkflowNodesSpec.model_validate` 二次校验,非法返 `{status:'error',error:'invalid nodes spec'}`;`WorkflowEngine.run(spec, ctx)` 调用);`WORKFLOW_RUN_SCHEMA`(JSON schema,见 2.2.1) | `services/orchestrator/src/tools/composite/v2_workflow.py`(新建,~60 行) | P0 | W-P0-3 | 单测:handler 非法 nodes(空数组)返 status='error';合法 nodes 薄桥调 engine.run(mock) |
| **W-P0-6** | `engine.py` 加 `_WORKFLOW_TOOLS` 清单(一行元组)+ `_n_wf = _bulk_register(_tool_registry, _WORKFLOW_TOOLS)`(layer=`ToolLayer.COMPOSITE`)。**register 名 `workflow_run`(无 v2_ 前缀,ToolBridge 自动加成 v2_workflow_run)** | `services/orchestrator/src/engine.py`(改 ~6 行,在 `_SKILL_TOOLS` 之后) | P0 | W-P0-5 | 单测:`_tool_registry.lookup('workflow_run')` 命中;经 `ToolBridgeCapability.get_toolset` 后模型可见 `v2_workflow_run`(dispatch 端 strip 前缀回 `workflow_run` 不崩) |
| **W-P0-7** | e2e 验证 | — | P0 | W-P0-6 | e2e:1 条 `/h/agent-os-v2` turn,主 agent 调 `v2_workflow_run` fan-out 3 子 agent(schema_ref='text'),验证(a)3 子 agent output 在主 agent 收到;(b)observe 收 4 事件(wire tick_completed,`data.flow_event` ∈ `WORKFLOW_FLOW_EVENTS`);(c)TUI 端 `workflow_*` 分支显式断言。**前置**:`_state.memory_observe_emitter` 通电(否则 e2e 改断言"emitter=None 时事件静默跳过") |

### P1(增量挂上,不改 P0 代码)

| node_id | primitives | files | tier | depends_on | verify |
|---|---|---|---|---|---|
| **W-P1-1** | `WorkflowEngine.pipeline`:无 barrier 阶段链(`asyncio.Queue` per stage 串联,每 item 独立跑完所有 stage,wall-clock=单 item 链);**闭包 bug 修正**:`async def _stage_worker(stage=stage)` 默认参数绑定循环变量;`_emit_workflow(workflow_pipeline_stage_started/completed)` | `workflow_engine.py`(同文件追加方法,~50 行) | P1 | W-P0-3 | 单测:3 stage × 3 item pipeline,验证每 item 独立跑完所有 stage(无 BARRIER 等齐),wall-clock ≈ 单 item 链长(非 sum) |
| **W-P1-2** | budget 强制:`WorkflowEngine.run`/`_spawn_agent` 接 `ctx.budget_limits: UsageLimits`;每 `_spawn_agent` 前 `check_before_request` 短路;`total_usage += node_usage`(lock 包);显式调高 parent Agent 的 `UsageLimits(request_limit=None 或 1000)` 防 P0 默认值 50 在 fan-out N=8+ 提前 `UsageLimitExceeded` | `workflow_engine.py`(~30 行,改 `_spawn_agent`/`run` 签名接 ctx) | P1 | W-P0-3 | 单测:设 `budget_limits=UsageLimits(total_tokens_limit=100)`,3 子 agent 累加超 100 → `run` 在第 2 个完成后 break 返 `status='success'`(部分结果)+ `total_usage` 标记 cap 触达 |
| **W-P1-3** | `WorkflowEngine.loop`:while + `ctx.seen: set[str]` 去重 + `ctx.dry_counter`(`>= dry_limit=2` break,新结果到达时 `dry=0` 重置 — 修正 [2] 不重置 bug)+ `budget.total_tokens_limit` guard(短路 break)+ `_emit_workflow(workflow_loop_started/iteration/completed)`;`seen_key_fn` 内建 `'content_hash'`(sha256[:16])/`'label'` | `workflow_engine.py`(~50 行) | P1 | W-P1-2 | 单测:mock finder 返重复候选 → dry counter 累加到 2 break;mock finder 返新候选 → dry=0 重置;budget 超限 break |
| **W-P1-4** | `tools/composite/v2_workflow.py` 加 `workflow_loop_handler` + `WORKFLOW_LOOP_SCHEMA`(2.2.2);`engine.py._WORKFLOW_TOOLS` 同清单加一行(`workflow_loop`,无 v2_ 前缀) | `v2_workflow.py`(追加 ~40 行)+ `engine.py`(改 1 行) | P1 | W-P1-3 | e2e:主 agent 调 `v2_workflow_loop` 3 轮,验证 seen 去重 + dry break |

### P2(拆子模块,跨进程 resume / 嵌套 / worktree)

| node_id | primitives | files | tier | depends_on | verify |
|---|---|---|---|---|---|
| **W-P2-1** | `workflow_engine/journal.py`:`WorkflowJournal` 类(照搬 `OrchSessionStore` SQLite WAL + `busy_timeout=5000` + 复用 `pysqlite3` monkey-patch);`workflow_run` + `workflow_event` 两表(DDL 见 6.1,含 `UNIQUE INDEX (run_id, key, type)`);`append_event` append-only;`resume(run_id, engine)` 事件溯源重放(完成 agent 走 cache 不重跑,见 6.2) | `services/orchestrator/src/harness/workflow_engine/__init__.py`(包化,把 P0+P1 单文件迁成包内 `engine.py`)+ `workflow_engine/journal.py`(新建,~120 行) | P2 | W-P1-3 | 单测:(a)中断在第 2 个 node started 后 result 前 → resume 重跑该 node;(b)已完成 node(有 result 事件)走 cache;(c)`UNIQUE INDEX` 防 cache hit 重复写 |
| **W-P2-2** | `workflow_engine/nesting.py`:`nested_run(spec, parent_ctx)`,`_WF_CTX: ContextVar[WorkflowContext \| None]`;`parent_ctx.depth >= 1` raise `WorkflowNestingError('one-level only')`;child_ctx 共享父 `total_usage`(同 RunUsage 引用)/ `concurrency`(同 Semaphore)/ `abort`(同 Event)/ `worktree_manager` / `journal`;`depth=parent_ctx.depth+1`;`_WF_CTX.set/reset` 包 try/finally 栈式回退 | `workflow_engine/nesting.py`(新建,~50 行) | P2 | W-P2-1 | 单测:(a)root→child 正常;(b)child 内再 `nested_run` raise;(c)并发 2 个 `nested_run`(asyncio.gather)验证 child_ctx 不串味(task-local 继承);(d)父 `abort.set()` 后 child `_spawn_agent` 短路 |
| **W-P2-3** | `workflow_engine/worktree.py`:`WorktreeManager` 类(`subprocess git worktree add --detach .claude/worktrees/<run_id>/<agent_id> HEAD`;`release` 调 `git worktree remove --force`;`__aenter__/__aexit__` 整 run cleanup token);**ADR-4 显式论证**:native in-process Agent 无 harness client,worktree 仅切 cwd;**cwd 透传修正**:`build_native_agent` 无 cwd/deps 参数(verified),`_spawn_agent` 内 `with chdir(wt): agent.run(...)`(`contextlib.contextmanager` 包 `os.chdir` + restore)— **per-agent 独占执行**(`asyncio.Semaphore(1)` 串行化 worktree node,因 `os.chdir` 是进程全局) | `workflow_engine/worktree.py`(新建,~80 行) | P2 | W-P1-2 | 单测:(a)`acquire/release` 调 git worktree add/remove;(b)`__aexit__` 释放所有未 release 的 worktree;(c)`chdir` 上下文还原;(d)并发 2 worktree node 串行化执行;**opt-in**(`isolation='worktree'` 才启用,默认 None 走 session cwd) |
| **W-P2-4** | `_emit_workflow` 同步双写:推 observe(内存)+ journal 落盘(P2 子模块通电后);`run` 起 `_emit_workflow(workflow_started, journal=True)` 后每 `_spawn_agent` 起止各 journal 一行(started + result);`resume` 重放重建 ctx | `workflow_engine.py` 改 `_emit_workflow` / `_spawn_agent` 加 journal hook(~20 行) | P2 | W-P2-1 | e2e:长 workflow 中断后 `resume(run_id)` 完成,已完成 agent 走 cache 不重跑 |

---

## 6. Journal 设计(P2 跨进程 resume)

### 6.1 表 DDL

```sql
-- workflow_engine/journal.py
CREATE TABLE IF NOT EXISTS workflow_run (
  run_id        TEXT PRIMARY KEY,         -- wf_<uuid12>
  session_id    TEXT NOT NULL,            -- orche session(observe 关联)
  parent_run_id TEXT,                     -- 嵌套:父 run_id(一层限制,depth<=1;NULL=顶层)
  spec_json     TEXT NOT NULL,            -- 完整 WorkflowNodesSpec JSON(replay 重建所需)
  status        TEXT NOT NULL DEFAULT 'running',  -- running|completed|failed|paused|aborted
  total_usage   TEXT,                     -- RunUsage 聚合 JSON {input,output,cache_read,total}
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_run_session ON workflow_run(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_wf_run_parent  ON workflow_run(parent_run_id);

CREATE TABLE IF NOT EXISTS workflow_event (
  event_id   TEXT PRIMARY KEY,            -- uuid4
  run_id     TEXT NOT NULL REFERENCES workflow_run(run_id),
  seq        INTEGER NOT NULL,            -- 单调递增(replay ORDER BY seq)
  agent_id   TEXT,                        -- 子 agent id(对位 CC journal.jsonl agentId)
  node_label TEXT,                        -- workflow node label(TUI 显示)
  type       TEXT NOT NULL,               -- 'started' | 'result'(对位 CC type 字段)
  key        TEXT NOT NULL,               -- 'v2:'+sha256(prompt+opts)[:16] — cache key
  payload    TEXT NOT NULL,               -- JSON:{label,status,output,usage,error,flow_event}
  timestamp  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_event_run ON workflow_event(run_id, seq);
-- R: 强制 cache 命中唯一性(grafted from design [2])— resume 不重跑完成 agent 的幂等保证
CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_event_key ON workflow_event(run_id, key, type);
```

> **连接策略**:与 `OrchSessionStore` 同 db 文件(`data/orch_sessions.db`),
> 经 `_ensure_column` 风格 migration 加两张表;复用 `start.py` 的 `pysqlite3`
> monkey-patch(不另起 patch 路径)。`WAL + busy_timeout=5000` 兜底单 loop 竞争。

### 6.2 事件类型(append-only)

| type | 触发点 | payload 关键字段 |
|---|---|---|
| `started` | `_spawn_agent` 起 | `{label, agent_id, model, prompt_sha256}` |
| `result` | `_spawn_agent` 止(成功或失败) | `{label, agent_id, status, output, usage, error?}`;`key='v2:'+sha256(prompt+opts)[:16]` |

### 6.3 replay 伪码

```python
# workflow_engine/journal.py
async def resume(self, run_id: str, engine: "WorkflowEngine") -> dict[str, Any]:
    """事件溯源 replay。完成 agent(type='result')走 cache 不重跑,
    半完成(只有 started 无 result)/未启动 agent 重跑。"""
    row = self._fetch_run(run_id)
    if row is None:
        raise ValueError(f"unknown run_id: {run_id}")
    if row["status"] in ("completed", "aborted"):
        return {"status": "replay_skip", "reason": f"run already {row['status']}"}

    events = self.list_events(run_id)  # SELECT * FROM workflow_event WHERE run_id=? ORDER BY seq
    spec = WorkflowNodesSpec.model_validate_json(row["spec_json"])

    # 按 agent_id 分组:有 result 的 → cache hit 跳过;只有 started 或无事件 → 重跑
    result_keys: dict[str, dict] = {}   # agent_id → cached payload
    started_keys: set[str] = set()
    for ev in events:
        if ev.type == "result":
            payload = json.loads(ev.payload)
            result_keys[ev.agent_id] = payload
        elif ev.type == "started":
            started_keys.add(ev.agent_id)

    cached_results: list[dict] = list(result_keys.values())
    pending_nodes: list[WorkflowNodeSpec] = [
        n for n in spec.nodes
        if n.label not in {p["label"] for p in cached_results}
    ]

    # 复用 engine.run 的并发逻辑,只跑 pending 子集
    ctx = WorkflowContext(
        session_id=row["session_id"],
        run_id=run_id,
        total_usage=RunUsage.from_json(row["total_usage"]) if row["total_usage"] else RunUsage(),
        journal=self,
    )
    fresh = await engine.run(
        WorkflowNodesSpec(nodes=pending_nodes, fan_in=spec.fan_in,
                          timeout_per_node_ms=spec.timeout_per_node_ms),
        ctx,
    )

    self.mark_completed(run_id, status="completed", total_usage=ctx.total_usage)
    return {
        "status": "resumed",
        "cached": len(cached_results),
        "fresh": len(fresh.node_results),
        "total_usage": _usage_dict(ctx.total_usage),
    }
```

**幂等保证**:`UNIQUE INDEX (run_id, key, type)` 在 DB 层兜底,即使应用层 cache hit
判定竞态(并发 resume 同一 run_id),重复 `result` 写入被 DB 拒(INSERT 冲突),
防半完成 agent 重跑产生双份结果。

---

## 7. 嵌套传播机制(P2,一层限制)

```python
# workflow_engine/nesting.py
_WF_CTX: ContextVar[WorkflowContext | None] = ContextVar("wf_ctx", default=None)

class WorkflowNestingError(RuntimeError):
    """嵌套深度超限(一层限制:root → child,child 内再嵌套 raise)。"""

async def nested_run(spec: WorkflowNodesSpec, parent_ctx: WorkflowContext) -> WorkflowResult:
    parent = _WF_CTX.get()
    if parent is None:
        parent = parent_ctx  # root 调用首次进入
    if parent.depth >= 1:
        raise WorkflowNestingError(
            "nested workflow depth > 1 (one-level limit: root → child only)"
        )

    # 共享父 ctx 的可变状态(同引用):
    #   total_usage (RunUsage)     — 父子 token 自然汇总到同一 budget
    #   concurrency (Semaphore)    — 父子总并发不超 cap
    #   abort (asyncio.Event)      — 父 set 后子 cooperative cancel 检查点
    #   worktree_manager / journal — 透传
    child_ctx = replace(
        parent,
        depth=parent.depth + 1,
        # total_usage / concurrency / abort / worktree_manager / journal 不 replace = 共享引用
        run_id=f"wf_{uuid4().hex[:12]}",  # 子 run 独立 id(journal 记录)
    )
    token = _WF_CTX.set(child_ctx)
    try:
        engine = _get_engine()  # 模块级单例(持 emitter / pitfail / tool_executor)
        return await engine.run(spec, child_ctx)
    finally:
        _WF_CTX.reset(token)
```

**ContextVar 语义 caveat**(judge cross-design risk):ContextVar 是 task-local 非
coroutine-local;`asyncio.gather` 并发子 task 时各 task 在创建时刻**拷贝**当前
ContextVar 值,sibling 之间的 `set/reset` 不互染。pydantic-ai `agent/__init__.py:501-525`
用的是 "Agent 实例级 deps 注入",与本设计 "跨 gather 子 task 共享同一 RunUsage /
Semaphore" 是两种语义 — 本设计靠**共享 Python 对象引用**(RunUsage/Semaphore/Event
都是 mutable),ContextVar 仅承载"找到父对象"的指针;并发不串味由 Python 引用语义
保证(对象同一,各 task 看到同一 Semaphore/Event)。单测 W-P2-2 显式覆盖。

**abort 传播**:`_spawn_agent` 每 fan-out 前检查 `if ctx.abort and ctx.abort.is_set():
return NodeResult(status='error', error='aborted by parent')`,父 `abort.set()` 后
所有 in-flight 子 workflow 短路。

---

## 8. WorktreeManager 接口(P2,per-agent opt-in)

```python
# workflow_engine/worktree.py
import os, subprocess
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

class WorktreeManager:
    """per-agent opt-in git worktree 隔离。

    仅当 WorkflowNodeSpec.isolation == 'worktree' 时启用(EXPENSIVE ~200-500ms/agent,
    含 git worktree add + 子 agent 文件操作)。默认 None 走 session cwd(YAGNI)。

    ADR-4 红线显式论证:native in-process Agent(build_native_agent 返 pydantic-ai Agent)
    无 harness client 概念,worktree 仅切 Python 进程 cwd,不创建新 ClaudeClient/ClawClient。
    """

    def __init__(self, base: Path) -> None:
        self.base = base  # 主 repo root
        self._refs: dict[str, Path] = {}
        self._wt_semaphore = asyncio.Semaphore(1)  # os.chdir 是进程全局,串行化 worktree node

    async def acquire(self, agent_id: str, run_id: str, base_ref: str = "HEAD") -> Path:
        # 统一路径(收敛 judge cross-design risk 三 design 各写各的):
        #   .claude/worktrees/<run_id>/<agent_id>(.claude/ 已在 gitignore 惯例内)
        wt = self.base / ".claude" / "worktrees" / run_id / agent_id
        if not wt.exists():
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), base_ref],
                cwd=self.base, check=True, capture_output=True,
            )
        self._refs[agent_id] = wt
        return wt

    async def release(self, agent_id: str, keep: bool = False) -> None:
        wt = self._refs.pop(agent_id, None)
        if wt and not keep:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=self.base, check=False, capture_output=True,  # 容错残留
            )

    async def __aenter__(self) -> "WorktreeManager":
        return self

    async def __aexit__(self, *exc) -> None:
        # 整 run cleanup token:释放所有未 release 的 worktree(防泄漏)
        for agent_id in list(self._refs.keys()):
            await self.release(agent_id)


# _spawn_agent 内 cwd 透传(修正 judge critique:build_native_agent 无 cwd/deps 参数):
@contextmanager
def _chdir(path: Path):
    """进程级 cwd 切换上下文。os.chdir 是全局,故 WorktreeManager._wt_semaphore(1)
    串行化所有 worktree node(per-agent 独占执行)。"""
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)

# 在 _spawn_agent:
#   if node.isolation == "worktree" and ctx.worktree_manager:
#       async with ctx.worktree_manager._wt_semaphore:
#           wt = await ctx.worktree_manager.acquire(node.label, ctx.run_id)
#           try:
#               with _chdir(wt):
#                   result = await agent.run(task_input, usage=node_usage)
#           finally:
#               await ctx.worktree_manager.release(node.label)
#   else:
#       result = await agent.run(task_input, usage=node_usage)  # 默认 session cwd
```

**关键修正**(judge critique):`build_native_agent` 签名(verified at
`native_agent.py:63-128`)无 `cwd` / `deps` 参数,`deps={'cwd':cwd}` 不可行。
`OrchSessionStore.cwd` 是 claude harness external cwd 非 native in-process Agent 的。
唯一可行通道是 `os.chdir` 进程级切换 — 因其全局性,worktree node 必须 `Semaphore(1)`
串行化(代价:worktree node 失去并发,但 worktree opt-in 本就 EXPENSIVE 边缘场景)。

---

## 9. 风险 + 缓解(承 Understand 蓝图 8 风险 + judge cross-design risks)

| # | 风险 | 缓解 | 落地阶段 |
|---|---|---|---|
| RK1 | `asyncio.gather` 嵌套在 pydantic-ai Agent.run 已运行的 event loop 中,误用 `run_sync` 触发 `no running event loop` | `workflow_engine.run` 必须 `async def`,经 `ToolExecutor.execute`(:40 async)异步调用,内部 `asyncio.gather` 沿用主 event loop。禁用 `agent.run_sync` / `asyncio.run`。单测 W-P0-3 在已有 event loop 内调 `run` 验证不崩 | P0 |
| RK2 | RunUsage 共享可变引用并发 `incr` 无锁,P0 即埋并发写竞态(judge cross-design risk) | **P0 即用 per-node RunUsage**:`_spawn_agent` 内 `node_usage = RunUsage()`,`agent.run(usage=node_usage)`,fan-in 阶段在 `asyncio.Lock` 内 `ctx.total_usage += node_usage`(`__add__` 返新对象,无原对象竞态)。**不**用 `agent.run(usage=ctx._shared_usage)` 共享可变引用。retry 子 agent 重复累加 → P1 baseline snapshot + 失败子 agent usage 标 `'attempt_failed'` 不计 `total_usage` | P0(per-node)+ P1(retry baseline) |
| RK3 | `UsageLimits` 默认 `request_limit=50` 在 fan-out N=8+ 提前 `UsageLimitExceeded` | **P0 即调高**:`_spawn_agent` 内 `agent.run(usage_limits=UsageLimits(request_limit=None))` 显式 unset request_limit(只让 `total_tokens_limit` 罩子任务,父 limit 留给顶层)。P1 接 budget 后 `ctx.budget_limits` 罩子任务,父 limit 独立 | P0 |
| RK4 | 智谱 glm anthropic 通道并发限流(429)+ ModelRetry 雪崩 | `asyncio.Semaphore(ctx.concurrency=8)` 默认 cap。429 由 pydantic-ai `ModelRetry` `except Exception` 容错(memory `pydantic-ai-v2-harness-adoption` 已确认治 429 / ValueError)。无动态 rate-limit header 反压(ponytail:固定 8 speculative,实测限额后调) | P0 |
| RK5 | observe 事件 wire `tick_completed` 的 hack 被 TUI 误判为普通 tick(语义混淆) | (a) `data.flow_event` 全部前缀化 `workflow_*` 避免与 flow.py `node_completed` 同字符串碰撞;(b) `WORKFLOW_FLOW_EVENTS` 冻结值集合在 P0 落地即加 TUI 端 `workflow_*` 分支显式断言;(c) 模块 docstring 文档化 wire 约定 | P0 |
| RK6 | pitfall 语义鸿沟:`tool_executor` 返 `{status:'error'}` 非 raise,`on_tool_execute_error` 不触发 | `run` 的 `gather(return_exceptions=True)` 后显式 `isinstance(r, Exception)` 转 `NodeResult(status='error')`(R7)。pitfail 计数经 `ToolBridgeCapability._record_pitfall` 自动接管,workflow 不重复计数。单测 W-P0-3 覆盖子 agent raise 时 `run` 返 `status='error'` 不崩 | P0 |
| RK7 | `Tool.from_schema` any_schema validator 跳过 pydantic 校验,模型传非法 nodes(minItems 不满足 / prompt 空串)绕校验 | 双校验:(a) JSON schema register 给模型提示(`nodes.minItems=1`,`prompt.minLength=1`);(b) handler 内 `WorkflowNodesSpec.model_validate` 兜底,非法返 `{status:'error',error:'invalid nodes spec'}` | P0 |
| RK8 | `EngineeringDisciplineCapability` 默认 prepend 的 CC 5 条纪律不适合 workflow 子任务(子任务是机械执行,纪律段膨胀 token,4096 node 时浪费 ~200-500 token × N) | **本轮不绕过**(`native_agent.py:114` 红线,R5)。纪律段对子 agent 普适(absolute paths / no report .md / no emoji)。若实测干扰子任务输出质量,**后续**在 `build_native_agent` 加 `workflow_context` 参数条件化 prepend(当前签名 verified 无此参数,P0 不动)。**P0 量化**:fan-out N=8 默认 ~8 × 300 token 纪律段 = 2.4k token,可接受 | P0(不绕过)+ 后续(条件化 prepend) |
| RK9 | P2 journal SQLite 跨进程并发写(长 run + observe 推事件)与 OrchSessionStore 同 db 锁竞争 | 照搬 `OrchSessionStore` `busy_timeout=5000` + `WAL` 兜底单 loop 竞争;复用 `start.py` 的 `pysqlite3` monkey-patch(不另起 patch 路径);`_ensure_column` 风格 migration 加表。若实测写冲突升级 `aiosqlite` 或独立 `orch_workflow.db` | P2 |
| RK10 | P2 嵌套 ContextVar 在 asyncio.gather 并发子 workflow 时串味 | 见 7 节 caveat:ContextVar 承载"找父对象"的指针,共享靠 Python 对象引用(RunUsage/Semaphore/Event 都是 mutable,各 task 看到同一对象)。单测 W-P2-2 显式覆盖并发 2 个 `nested_run` 不串味 | P2 |
| RK11 | `v2_` 前缀双重叠加(judge cross-design risk):ToolBridge 已 `.prefixed("v2")`,register 名带 v2_ 致 `v2_v2_workflow_run` | register 名必须用 `workflow_run` / `workflow_loop`(**无 v2_ 前缀**),ToolBridge 自动加成 `v2_workflow_run`(verified at `tool_bridge_capability.py:83`)。W-P0-6 单测验证模型可见名 = `v2_workflow_run` 不带双前缀 | P0 |
| RK12 | fan-in 后"主 agent 单点落库"接入点缺失(judge cross-design risk):三 design 只覆盖"子 agent 不写"未覆盖"主 agent 怎么写" | **P0 明确**:`WorkflowEngine.run` 返回 `WorkflowResult` dict 经 ToolExecutor 包装为主 agent 收到的工具输出。主 agent 在 `/h` 主 turn 内决定是否调 `memory_*` 工具落库(future 主 agent 业务逻辑,workflow_engine 不掺和)。**P0 范围**:workflow_engine 只产结构化结果,不调 memory_*;主 agent 单点落库接入点 = 主 agent 自己的工具调用(future),不在 workflow 内核范围。**open question Q1** | P0(声明)+ 后续(主 agent 落库) |

---

## 10. 关键决策

1. **骨架**:design [1] 单文件 `workflow_engine.py`(P0+P1)+ P2 拆 `nesting.py` /
   `worktree.py` / `journal.py`(judge 共识 winner,reuse 5 + over-engineering 4-5 +
   redline 5 + feasibility 4)。
2. **红线 R1/R2 写成 CI grep 机械守恒**(graft from [2]):`grep -rE 'memory_*' src/harness/workflow_engine*.py` 必须空 / `grep -rE '_build_execution_graph|_node_llm|trigger_turn'` 必须空。docstring 注释 + grep 双保险。
3. **`workflow_event.UNIQUE INDEX (run_id, key, type)`**(graft from [2]):DB 层兜底 cache 命中唯一性,防半完成 agent 重跑产生双份结果。
4. **`WorkflowContext` 字段公开化**(修正 [0]/[2]):`shared_usage` / `total_usage` 无下划线,P1/P2 增量字段先 None,P0 不用的字段不在 P0 dataclass 里 — 但因 P0/P1 同文件,字段先定义但 P0 不读(judge 接受单文件内 forward-ref 程度轻)。
5. **`v2_` 前缀去重**:register 名 `workflow_run` / `workflow_loop`(无 v2_),ToolBridge 自动加成 `v2_workflow_run`(verified)。
6. **worktree cwd 透传 `os.chdir` + Semaphore(1) 串行化**(修正 [2] `deps={'cwd':cwd}` 不可行):`build_native_agent` 无 cwd/deps 参数(verified),唯一通道是进程级 `os.chdir`,worktree node 牺牲并发换隔离(opt-in 边缘场景可接受)。
7. **per-node RunUsage + Lock 内 fan-in 聚合**(RK2 mitigation):避开共享可变引用并发竞态。
8. **`flow_event` 前缀化** `workflow_*` / `workflow_node_*`:避免与 flow.py `node_completed` 同字符串碰撞。
9. **dry counter `dry_limit=2`**(收敛三 design 2 vs 3 分歧):新结果到达 `dry=0` 重置(修正 [2] 不重置 bug),连续 2 轮无新结果 break(ponytail:更早收敛省 budget)。
10. **R1 范围**:workflow_engine 只产结构化结果不调 memory_*;主 agent 单点落库是主 agent 业务逻辑(future),不在内核范围(open question Q1)。

---

## 11. 开放问题

| Q | 问题 | 默认解决方案 |
|---|---|---|
| Q1 | fan-in 结果的主 agent 单点落库接入点(future):主 agent 收到 `WorkflowResult` dict 后调什么 API 把结果落 memory? | 默认:**不**在 workflow 内核加 memory_* 调用(R1 严守)。主 agent 在 `/h` 主 turn 内自行决定是否调 `memory_event_bus` / `memory_service` 把 fan-in 结果作为单点 ingest。P0 ship 时不实现,留待主 agent 业务侧迭代。 |
| Q2 | 主 agent 收到 `WorkflowResult` 里的 `node_results: list[NodeResult]`(success output + error status 混杂),`fan_in='merge'` 时 error node 的 dict 字段如何合并? | 默认:`fan_in='merge'` 只合并 `status='success'` 的 node 的 output dict 字段(`later-wins` 语义);error node 的 `error` 字段单独聚合成顶层 `errors: list[str]`。`fan_in='list'` 原样数组(success/error 混杂,消费者按 `status` 字段过滤)。 |
| Q3 | concurrency cap 默认 8 是否合适(智谱 glm anthropic 通道实际限额未实测)? | 默认:固定 8(ponytail:speculative,YAGNI 探测)。若实测 429 雪崩 RK4 升级动态反压(rate-limit header 解析)。 |
| Q4 | `_state.memory_observe_emitter` 通电是 P0 e2e 4 事件断言的前置(_state.py default = None),CI / 容器环境 emitter=None 时事件全丢 — e2e 断言如何写? | 默认:e2e 分双路径:(a) emitter 通电环境 → 断言 observe 收 4 事件;(b) emitter=None 环境 → 断言 `_emit_workflow` 不崩 + 静默跳过(不 fail)。CI 默认走 (b),(a) 在通电集成环境跑。 |
| Q5 | `EngineeringDisciplineCapability` 默认 prepend 对 4096 node fan-out 的 token 成本(~200-500 token × N)是否需 P0 即条件化? | 默认:**不**条件化(R5 红线不绕过)。P0 默认 N 上限 4096 但实际 fan-out 通常 N=4-16;若实测发现显著 token 浪费 / 干扰子任务输出质量,后续在 `build_native_agent` 加 `workflow_context` 参数条件化 prepend(P0 不动 native_agent.py)。 |
| Q6 | P2 `WorktreeManager._wt_semaphore(1)` 串行化 worktree node 失去并发 — opt-in 是否值得? | 默认:**opt-in 默认 None**(YAGNI)。仅当 `node.isolation='worktree'` 显式启用。worktree 主要场景是"并行改文件冲突",但串行化后实际不并行 — 故 worktree 真正场景是**多 workflow 跨进程**(每 workflow 独立进程级 cwd),非单 workflow 内并发。P2 落地时若发现此特性无价值,defer / 删除。 |
