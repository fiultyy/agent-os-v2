# Agent OS 实施大纲

> 基于 architecture.md + 12 个设计决策，按最小 MVP 增量式拆解
> 创建日期: 2026-04-02
> 状态: 待确认

---

## 阶段总览

```
Phase 0: 基础设施          ← 跑通最小开发环境
Phase 1: 编排骨架          ← 图状态机能跑起来
Phase 2: 记忆 MVP          ← 单 Agent 最小记忆能力
Phase 3: 上下文工程        ← ContextCompiler 可用
Phase 4: 工具执行闭环      ← ToolExecutor 异步闭环
Phase 5: 前端画布 MVP      ← React Flow 可交互
Phase 6: 集成联调          ← 全链路跑通一个完整场景
Phase 7: 记忆增强 (V1)     ← 向量检索 + 压缩 + 多层记忆
Phase 8: 多 Agent 协作     ← 通信 + 并发 + 共享记忆
Phase 9: 生产化 (V2)       ← PG + KG + 完整权限
```

每个 Phase 内部按**原子任务**拆解，每个任务独立可验证。

---

## Phase 0: 基础设施搭建

> 目标: 开发环境能跑，服务能启动，前端能渲染

### 0.1 后端开发环境

**T-0.1.1 Python 虚拟环境 + 依赖安装**
- 操作: 为每个 service 创建 venv，安装 pyproject.toml 中声明的依赖
- 验收: `pip install -e services/gateway/` 等 5 个服务均可无报错安装
- 预计: 30min

**T-0.1.2 Gateway 启动验证**
- 操作: `cd services/gateway && python -m src.main`，确认 FastAPI 在 8000 端口启动
- 验收: `curl http://localhost:8000/health` 返回 `{"status": "ok"}`
- 预计: 15min

**T-0.1.3 Orchestrator 启动验证**
- 操作: `cd services/orchestrator && python -m src.engine`，确认可启动
- 验收: 无 import 错误，打印 "Orchestrator engine initialized"
- 预计: 15min

**T-0.1.4 docker-compose 验证**
- 操作: `docker-compose up` 启动 gateway + orchestrator
- 验收: 两个容器均 running，gateway health check 通过
- 预计: 30min

### 0.2 前端开发环境

**T-0.2.1 pnpm install + dev server**
- 操作: `cd apps/web && pnpm install && pnpm dev`
- 验收: `http://localhost:3000` 可访问，无报错
- 预计: 20min

**T-0.2.2 React Flow 渲染验证**
- 操作: 打开首页，确认 FlowCanvas 组件渲染空画布
- 验收: 画布区域可见（网格背景），可拖拽平移和缩放
- 预计: 15min

**T-0.2.3 自定义节点渲染**
- 操作: 在画布上硬编码渲染 1 个 AgentNode + 1 个 ToolNode
- 验收: 两个节点可见，显示标题和图标，可拖拽移动
- 预计: 30min

### Phase 0 验收标准
- [ ] 5 个后端服务均可独立启动（或通过 docker-compose）
- [ ] 前端 `pnpm dev` 可运行，React Flow 画布可交互
- [ ] 硬编码的 Agent/Tool 节点可拖拽

---

## Phase 1: 编排骨架

> 目标: 图状态机能执行最简单的 "输入 → 处理 → 输出" 流程

### 1.1 图状态机核心

**T-1.1.1 GraphState 数据结构**
- 操作: 实现 `services/orchestrator/src/graph/state.py`
- 内容: 定义 `GraphState` dataclass，包含 `messages: list`, `current_node: str`, `context: dict`, `status: str`
- 验收: 可实例化，字段可读写，有 `to_dict()` / `from_dict()` 方法
- 预计: 30min

**T-1.1.2 GraphNode 抽象**
- 操作: 实现 `services/orchestrator/src/graph/nodes.py`
- 内容: `GraphNode` 基类，定义 `async execute(state: GraphState) -> GraphState` 接口
- 验收: 可创建子类（如 `LLMNode`, `ToolCallNode`），`execute` 方法签名正确
- 预计: 20min

**T-1.1.3 ConditionalEdge 条件路由**
- 操作: 实现 `services/orchestrator/src/graph/edges.py`
- 内容: `Edge` (无条件) + `ConditionalEdge` (基于 state 字段值路由到不同 node)
- 验收: 给定 state.status="needs_tool" → 路由到 ToolNode；state.status="done" → 结束
- 预计: 30min

**T-1.1.4 StateGraph 编排器**
- 操作: 实现 `services/orchestrator/src/graph/__init__.py` 的 `StateGraph` 类
- 内容: `add_node()`, `add_edge()`, `set_entry_point()`, `async run(initial_state)` 方法
- 验收: 构建一个 3 节点图 (Start → Process → End)，`run()` 按顺序执行，state 正确传递
- 预计: 1h

**T-1.1.5 最小可运行图**
- 操作: 创建 `services/orchestrator/src/engine.py` 中的示例图
- 内容: 构建 "用户输入 → LLM 调用(模拟) → 返回响应" 的最小图
- 验收: `python -m src.engine` 执行，输出 "用户输入经过处理后返回响应"
- 预计: 30min

### 1.2 Checkpoint 机制

**T-1.2.1 内存 Checkpoint 存储**
- 操作: 实现 `InMemoryCheckpointStore`，每次节点执行后保存 state 快照
- 验收: 图执行中断后，可从最近 checkpoint 恢复继续执行
- 预计: 45min

### Phase 1 验收标准
- [ ] StateGraph 可构建多节点图并按顺序执行
- [ ] ConditionalEdge 可根据 state 条件路由
- [ ] Checkpoint 机制可保存和恢复执行状态
- [ ] 最小示例图可通过命令行运行

---

## Phase 2: 记忆 MVP

> 目标: 单 Agent 具备最基础的 L0 (Working) + L1 (Session) 两层记忆

### 2.1 记忆数据类型

**T-2.1.1 核心类型定义**
- 操作: 实现 `services/orchestrator/src/memory/types.py`
- 内容: `MemoryRef`, `MemoryItem`, `MemoryBlock`, `MemoryScope`, `MemoryType`, `MemoryFilter` 数据类
- 验收: 可实例化各类型，字段类型正确，无 import 错误
- 预计: 30min

**T-2.1.2 MemoryBlock 管理**
- 操作: 实现 Core Memory Block 的 create / read / update
- 内容: 每个 Agent 有固定的 persona block + user_profile block，类似 Letta
- 验收: 可创建 block、读取内容、更新内容，超出 char_limit 时拒绝写入
- 预计: 30min

### 2.2 内存存储

**T-2.2.1 InMemoryStore 实现**
- 操作: 实现 `services/orchestrator/src/memory/store.py` 的 `InMemoryStore`
- 内容: `store()`, `get()`, `update()`, `delete()`, `list_by_scope()`, `get_block()`, `update_block()`
- 验收: 写入后可按 id 读取，update 后内容更新，delete 后不可读取
- 预计: 45min

**T-2.2.2 Session Memory 生命周期**
- 操作: 实现 Session Memory 的创建、追加、查询、销毁
- 内容: 会话开始时创建 Session，每轮对话追加消息，会话结束时归档
- 验收: 写入 10 条消息后，`get_recent(5)` 返回最近 5 条；会话结束后状态标记为 archived
- 预计: 30min

### 2.3 MemoryService 门面

**T-2.3.1 基础 MemoryService**
- 操作: 实现 `services/orchestrator/src/memory/service.py`
- 内容: `store()`, `get()`, `recall()` (MVP 用关键词匹配), `update()`
- 验收: store 后 recall 同一关键词可返回对应记忆
- 预计: 45min

**T-2.3.2 MemoryService 集成到图状态机**
- 操作: 在 StateGraph 的节点中注入 MemoryService，节点执行后自动 store
- 验收: 图执行 3 轮后，MemoryService 中有 3 条 session 记忆
- 预计: 30min

### Phase 2 验收标准
- [ ] MemoryBlock 可创建/读取/更新，有 char_limit 保护
- [ ] InMemoryStore 的 CRUD 操作正常
- [ ] MemoryService.recall() 可按关键词匹配返回记忆
- [ ] 图状态机执行后自动在 MemoryService 中积累 session 记忆

---

## Phase 3: 上下文工程

> 目标: ContextCompiler 能根据任务编译最小可用上下文

### 3.1 ContextManager

**T-3.1.1 Write 操作**
- 操作: 实现 `services/orchestrator/src/context/manager.py` 的 `write_to_external()`
- 内容: 将 context window 中的信息写入 MemoryService（超出当前关注范围的内容）
- 验收: 写入后 context window 的 token 数减少，MemoryService 中多了一条记忆
- 预计: 30min

**T-3.1.2 Select 操作**
- 操作: 实现 `select_from_external()`
- 内容: 从 MemoryService.recall() 获取相关记忆，注入到 context window
- 验收: 给定任务描述 "用户上次问的部署问题"，可从 MemoryService 召回相关记忆
- 预计: 30min

**T-3.1.3 Compress 操作**
- 操作: 实现 `compress_context()`
- 内容: MVP 用简单的截断策略（保留 system prompt + 最近 N 条 + 重要标记的记忆）
- 验收: 100 条消息的 session 压缩到 20 条后，核心信息保留，token 数显著下降
- 预计: 45min

**T-3.1.4 Isolate 操作**
- 操作: 实现 `isolate_context()`
- 内容: 为子任务创建独立的 context window（隔离子 Agent 的上下文）
- 验收: 父 Agent 隔离后的 context 与原始 context 互不影响
- 预计: 30min

### 3.2 ContextCompiler

**T-3.2.1 compile() 方法**
- 操作: 实现 `services/orchestrator/src/context/compiler.py`
- 内容: `compile(task, agent_state, token_budget)` → 输出 CompiledContext
- 流程: 获取 MemoryBlocks → recall 相关记忆 → 按 token_budget 截断 → 格式化为 prompt
- 验收: 给定 task="设计 MemoryService 接口"，输出包含相关记忆的编译后上下文，token 数在 budget 内
- 预计: 1h

**T-3.2.2 与图状态机集成**
- 操作: 每个节点执行前调用 ContextCompiler.compile() 编译上下文
- 验收: 节点收到的 state.context 是编译后的最小上下文，非全量历史
- 预计: 30min

### Phase 3 验收标准
- [ ] ContextManager 四策略 (write/select/compress/isolate) 各有基础实现
- [ ] ContextCompiler.compile() 可根据 task 编译最小可用上下文
- [ ] 编译后的上下文 token 数 ≤ 设定的 token_budget
- [ ] 图状态机每个节点执行前自动编译上下文

---

## Phase 4: 工具执行闭环

> 目标: ToolExecutor 能异步执行工具、收集结果、通过 Guardrail 检查

### 4.1 ToolRegistry

**T-4.1.1 工具注册表**
- 操作: 实现 `services/orchestrator/src/tools/registry.py`
- 内容: `register(name, func, schema)`, `get(name)`, `list_tools()` → 返回工具描述（供 LLM 调用）
- 验收: 注册 3 个工具后，`list_tools()` 返回 3 个工具的 name + description + parameters schema
- 预计: 30min

**T-4.1.2 内置工具集**
- 操作: 实现 3 个示例工具: `web_search(query)`, `read_file(path)`, `execute_code(code)`
- 验收: 每个工具可独立调用并返回结果
- 预计: 45min

### 4.2 ToolExecutor

**T-4.2.1 异步执行**
- 操作: 实现 `services/orchestrator/src/tools/executor.py`
- 内容: `async execute(tool_name, params, state)` → 调用注册的工具 → 返回结果
- 验收: 调用 execute("web_search", {"query": "test"}) → 返回搜索结果字符串
- 预计: 30min

**T-4.2.2 闭环: LLM 决定 → 执行 → 结果反馈 → LLM 继续**
- 操作: 在图状态机中添加 "ToolCallNode" + "ToolResultNode"
- 流程: LLMNode 输出 tool_call → ToolCallNode 执行 → ToolResultNode 收集结果 → 路由回 LLMNode
- 验收: 构建一个 "用户提问 → LLM 决定搜索 → 执行搜索 → 结果返回 LLM → 生成回答" 的完整闭环
- 预计: 1h

**T-4.2.3 工具结果生命周期（MVP）**
- 操作: 工具结果写入 Session Memory，带 safety_deadline（5 轮推理）
- 验收: 工具执行后 MemoryService 中有对应的 session 记忆，5 轮内不可被遗忘
- 预计: 30min

### 4.3 Guardrail

**T-4.3.1 输入 Guardrail**
- 操作: 实现 `services/orchestrator/src/tools/guardrail.py`
- 内容: 执行工具前检查参数合法性（如文件路径不能包含 ..，代码不能包含 rm -rf）
- 验收: 恶意参数被拦截，返回 guardrail_violation 错误
- 预计: 30min

**T-4.3.2 输出 Guardrail**
- 操作: 工具执行后检查结果安全性（如不包含敏感信息）
- 验收: 工具返回敏感数据时被拦截
- 预计: 20min

### Phase 4 验收标准
- [ ] ToolRegistry 可注册/查询/调用工具
- [ ] ToolExecutor 可异步执行工具并返回结果
- [ ] LLM → Tool → Result → LLM 完整闭环可运行
- [ ] Guardrail 可拦截恶意输入和敏感输出
- [ ] 工具结果写入 Session Memory，有安全期保护

---

## Phase 5: 前端画布 MVP

> 目标: React Flow 画布可以可视化编排流程

### 5.1 节点组件

**T-5.1.1 AgentNode 组件**
- 操作: 实现 `apps/web/src/components/canvas/nodes/AgentNode.tsx`
- 内容: 显示 Agent 名称、状态指示灯（idle/running/error）、当前 Working Memory 条目数
- 验收: 节点渲染正确，状态变化时指示灯颜色变化
- 预计: 45min

**T-5.1.2 ToolNode 组件**
- 操作: 实现 `apps/web/src/components/canvas/nodes/ToolNode.tsx`
- 内容: 显示工具名称、最近执行状态、耗时
- 验收: 节点渲染正确
- 预计: 30min

**T-5.1.3 PromptNode 组件**
- 操作: 实现 `apps/web/src/components/canvas/nodes/PromptNode.tsx`
- 内容: 显示 prompt 模板名称、变量列表
- 验收: 节点渲染正确
- 预计: 30min

### 5.2 画布交互

**T-5.2.1 拖拽添加节点**
- 操作: 从侧边栏拖拽 Agent/Tool/Prompt 到画布，创建新节点
- 验收: 拖拽后画布上出现新节点，Zustand store 中多了一条节点数据
- 预计: 45min

**T-5.2.2 节点间连线**
- 操作: 从节点 output handle 拖线到另一节点 input handle，创建 DataEdge
- 验收: 连线后两个节点间出现边，Zustand store 中多了一条边数据
- 预计: 30min

**T-5.2.3 节点属性编辑面板**
- 操作: 双击节点弹出属性面板，可编辑名称、参数等
- 验收: 修改名称后节点标题实时更新
- 预计: 45min

### 5.3 状态管理

**T-5.3.1 flowStore (Zustand)**
- 操作: 实现 `apps/web/src/stores/flowStore.ts`
- 内容: nodes[], edges[], onNodesChange, onEdgesChange, addNode, addEdge
- 验收: 节点增删改查操作正确，状态变化触发 React Flow 重新渲染
- 预计: 30min

**T-5.3.2 agentStore (Zustand)**
- 操作: 实现 `apps/web/src/stores/agentStore.ts`
- 内容: agents[], selectedAgent, updateAgentStatus
- 验收: 可切换选中 Agent，状态更新时节点面板同步
- 预计: 30min

### Phase 5 验收标准
- [ ] 3 种自定义节点（Agent/Tool/Prompt）可正确渲染
- [ ] 可从侧边栏拖拽添加节点到画布
- [ ] 节点间可连线，边数据正确保存在 Zustand store
- [ ] 双击节点可编辑属性，修改实时生效
- [ ] flowStore + agentStore 状态管理正确

---

## Phase 6: 集成联调

> 目标: 前端画布 → API Gateway → Orchestrator → 记忆 + 工具 全链路跑通

### 6.1 API 层

**T-6.1.1 Agent CRUD API**
- 操作: 实现 `services/gateway/src/routes/agents.py`
- 内容: POST /api/agents (创建), GET /api/agents/:id (查询), GET /api/agents (列表)
- 验收: curl 创建 Agent 后可查询到，列表 API 返回包含该 Agent
- 预计: 45min

**T-6.1.2 执行 API (SSE)**
- 操作: 实现 `POST /api/execute` → SSE 推送执行过程
- 内容: 接收用户消息，调用 Orchestrator 执行，通过 SSE 推送每个节点的执行状态
- 验收: curl 发送请求后，SSE 流式返回每个节点的执行状态（node_name, status, output）
- 预计: 1h

**T-6.1.3 前端 API 客户端**
- 操作: 实现 `apps/web/src/lib/api.ts`
- 内容: createAgent(), getAgents(), executeWithSSE() 方法
- 验收: 前端调用 createAgent() 后 Agent 出现在画布上
- 预计: 30min

### 6.2 全链路联调

**T-6.2.1 端到端场景: "用户提问 → Agent 搜索 → 返回答案"**
- 操作: 在前端画布创建 Agent + Tool(Search)，连线，输入问题，观察执行过程
- 验收:
  1. 前端画布显示 Agent 节点状态变 "running"
  2. SSE 推送 LLM 推理结果
  3. Agent 决定调用搜索工具 → Tool 节点状态变 "running"
  4. 搜索结果返回 → Agent 生成最终回答
  5. 全程记忆被写入 MemoryService
- 预计: 2h

**T-6.2.2 记忆验证**
- 操作: 执行 2 次对话后，检查 MemoryService 中的 session 记忆
- 验收:
  1. 第 2 次对话时，ContextCompiler 自动召回了第 1 次对话的相关记忆
  2. Agent 可以引用第 1 次对话的内容
- 预计: 1h

### Phase 6 验收标准
- [ ] API Gateway 的 CRUD + Execute API 可正常工作
- [ ] SSE 实时推送执行状态到前端
- [ ] 前端画布可创建 Agent、连线、触发执行
- [ ] 完整闭环: 用户输入 → LLM 推理 → 工具调用 → 结果返回
- [ ] 记忆在多次对话间正确积累和召回

---

## Phase 7: 记忆增强 (V1)

> 目标: 向量检索 + 多层记忆 + 压缩引擎 + 持久化存储

### 7.1 持久化存储

**T-7.1.1 SQLiteStore 实现**
- 操作: 实现 `SQLiteStore`，替代 `InMemoryStore`
- 内容: 创建 memories 表 + memory_blocks 表，CRUD 操作写入 SQLite
- 验收: 重启服务后，之前的记忆仍然存在且可查询
- 预计: 1h

**T-7.1.2 sqlite-vec 向量索引**
- 操作: 集成 sqlite-vec 扩展，为记忆内容生成 embedding 并存储
- 验收: 写入 100 条记忆后，语义搜索 "部署问题" 可返回相关记忆（非关键词匹配）
- 预计: 1.5h

### 7.2 多层记忆

**T-7.2.1 Episodic Memory (L2)**
- 操作: 实现会话结束时的 L1 → L2 迁移
- 内容: 会话结束时提取关键经验（摘要 + 关键实体 + 时间戳）写入 L2
- 验收: 会话结束后，Episodic 表中有一条摘要记录，包含会话的关键信息
- 预计: 1h

**T-7.2.2 混合检索 (向量 + 时序)**
- 操作: 实现 `HybridRetriever`，融合向量相似度和时间衰减
- 验收: 查询时，近期且相关的记忆排在前面，过时但相关的排在后面
- 预计: 1h

### 7.3 压缩引擎

**T-7.3.1 LLMCompressor (SUMMARIZE 策略)**
- 操作: 实现 LLM 驱动的摘要压缩
- 验收: 10 条对话消息压缩为 1 条摘要，关键信息保留
- 预计: 1h

**T-7.3.2 双触发机制**
- 操作: 实现 70% 异步压缩 + 85% 同步压缩（上限 2 秒）
- 验收: context 达到 70% 时后台启动压缩；达到 85% 时阻塞压缩，超时则跳过
- 预计: 1h

### 7.4 评分系统

**T-7.4.1 WeightedScorer 实现**
- 操作: 实现五维度评分（relevance/recency/uniqueness/confidence/frequency）
- 验收: 写入记忆后自动打分，分值在 0-1 范围内
- 预计: 1h

**T-7.4.2 可配置权重模板**
- 操作: 实现 coding/research/assistant 三种权重模板
- 验收: 同一条记忆在 "coding" 和 "assistant" 模板下评分不同
- 预计: 30min

**T-7.4.3 指数衰减**
- 操作: recency 维度使用指数衰减函数
- 验收: 7 天前的记忆 recency 评分约为 0.5（半衰期 7 天）
- 预计: 20min

### 7.5 遗忘

**T-7.5.1 SOFT_DELETE 遗忘**
- 操作: 实现基于重要性评分的自动遗忘（score < 0.1 持续 N 天 → 软删除）
- 验收: 100 条记忆中，10 条低分记忆被标记为 inactive，不再参与检索
- 预计: 45min

### 7.6 版本控制

**T-7.6.1 记忆版本管理**
- 操作: 每次更新记忆时保留旧版本到 memory_versions 表，记录 operator 字段
- 验收: 更新 3 次后可查询到 3 个历史版本，rollback(memory_id, version=1) 可恢复
- 预计: 1h

### Phase 7 验收标准
- [ ] 记忆持久化到 SQLite，重启不丢失
- [ ] 向量语义搜索可用（非关键词匹配）
- [ ] L1 → L2 迁移在会话结束时自动触发
- [ ] 压缩引擎可生成摘要，双触发机制工作正常
- [ ] 五维度评分 + 可配置模板 + 指数衰减正确计算
- [ ] 低分记忆自动软删除
- [ ] 记忆版本控制和回滚可用

---

## Phase 8: 多 Agent 协作

> 目标: 多个 Agent 可并行执行、通信、共享记忆

### 8.1 通信

**T-8.1.1 CommunicationBus 实现**
- 操作: 实现 `services/orchestrator/src/communication/bus.py`
- 内容: `send(from_agent, to_agent, message)`, `broadcast(from_agent, message)`, `receive(agent_id)`
- 验收: Agent A 发送消息后，Agent B 可 receive 到
- 预计: 1h

**T-8.1.2 消息作用域 (ScopeManager)**
- 操作: 实现按信任域过滤消息（Workspace/Session/Agent/Global）
- 验收: Agent 级消息只有目标 Agent 收到；Workspace 级消息同项目 Agent 都收到
- 预计: 45min

### 8.2 并发

**T-8.2.1 ConcurrencyController**
- 操作: 实现信号量限制 + 依赖检测
- 内容: 限制单 Agent 并发工具调用数（默认 5），检测工具间参数依赖
- 验收: 10 个独立工具调用并行执行，5 个依赖调用串行执行
- 预计: 1h

### 8.3 共享记忆

**T-8.3.1 Workflow 级记忆共享**
- 操作: 同一 Workflow 内的 Agent 共享 Semantic Memory（通过信任域）
- 验收: Agent A 写入共享记忆后，Agent B 可 recall 到
- 预计: 45min

**T-8.3.2 Blackboard 模式**
- 操作: 实现 Workflow 级的共享黑板，Agent 可发布/订阅关键信息
- 验收: Agent A 发布 "发现部署问题" → Agent B 订阅后自动收到通知
- 预计: 1h

### 8.4 权限

**T-8.4.1 5 级权限控制**
- 操作: 实现 Level 0-4 的读写删权限，包含跨信任域的摘要/详情访问控制
- 验收: Agent B 读取 Agent A 的 Semantic Memory，默认只看到摘要（Level 2）；显式授权后可看详情（Level 3）
- 预计: 1h

### Phase 8 验收标准
- [ ] CommunicationBus 可在 Agent 间传递消息
- [ ] 信任域过滤正确（Agent/Session/Workspace/Global）
- [ ] 并发工具调用可并行执行，有依赖的串行执行
- [ ] Workflow 级共享记忆可用
- [ ] Blackboard 发布/订阅工作正常
- [ ] 5 级权限控制 + 跨域摘要/详情分离

---

## Phase 9: 生产化 (V2)

> 目标: 生产级存储 + KG + 前端完整可视化 + 性能优化

### 9.1 PostgreSQL 迁移

**T-9.1.1 PostgresStore 实现**
- 操作: 替换 SQLiteStore 为 PostgresStore
- 验收: 所有 Phase 7 的测试在 PG 上通过，支持并发写入
- 预计: 1.5h

**T-9.1.2 pgvector 集成**
- 操作: 替换 sqlite-vec 为 pgvector
- 验收: 向量搜索性能 < 50ms（10 万条记忆）
- 预计: 1h

### 9.2 KG 层

**T-9.2.1 Graphiti + Neo4j 集成**
- 操作: 集成 Graphiti 作为 Semantic Memory 的 KG 存储层
- 验收: 记忆写入后自动提取实体和关系，图查询可返回关联实体
- 预计: 2h

**T-9.2.2 混合检索 (向量 + KG + 时序)**
- 操作: 实现三路混合检索，融合向量、图遍历、时序三路结果
- 验收: 关系推理查询（"张三在哪个项目"）可通过 KG 返回准确答案
- 预计: 1.5h

### 9.3 前端完整可视化

**T-9.3.1 独立 Memory 面板**
- 操作: 实现 Memory Panel 页面，展示四层记忆
- 验收: 可按层级筛选，每条记忆显示内容 + score + 时间 + 来源
- 预计: 2h

**T-9.3.2 调试模式**
- 操作: 实现调试开关，显示 compress/forget/migrate 事件流
- 验收: 开启调试模式后，每次压缩/遗忘操作实时显示在面板上
- 预计: 1h

**T-9.3.3 Agent 节点记忆面板**
- 操作: Agent 节点上显示 Working Memory 大小 + 最近 3 条摘要
- 验收: 节点面板实时更新，点击 "更多" 跳转 Memory 面板
- 预计: 45min

### Phase 9 验收标准
- [ ] PostgreSQL + pgvector 替代 SQLite，性能达标
- [ ] Graphiti KG 自动提取实体关系，图查询可用
- [ ] 三路混合检索工作正常
- [ ] Memory 面板完整展示四层记忆
- [ ] 调试模式实时显示状态变化事件
- [ ] Agent 节点面板显示记忆摘要

---

## 时间估算

| Phase | 预计工时 | 累计 |
|-------|---------|------|
| Phase 0: 基础设施 | 3h | 3h |
| Phase 1: 编排骨架 | 4h | 7h |
| Phase 2: 记忆 MVP | 4h | 11h |
| Phase 3: 上下文工程 | 4.5h | 15.5h |
| Phase 4: 工具闭环 | 4.5h | 20h |
| Phase 5: 前端画布 | 5h | 25h |
| Phase 6: 集成联调 | 5h | 30h |
| **MVP 合计** | **~30h** | |
| Phase 7: 记忆增强 | 11h | 41h |
| Phase 8: 多 Agent | 6h | 47h |
| Phase 9: 生产化 | 10h | 57h |
| **全部合计** | **~57h** | |

---

## 依赖关系

```
Phase 0 (基础设施)
  ↓
Phase 1 (编排骨架) ← 无外部依赖
  ↓
Phase 2 (记忆 MVP) ← 依赖 Phase 1 的图状态机
  ↓
Phase 3 (上下文工程) ← 依赖 Phase 2 的 MemoryService
  ↓
Phase 4 (工具闭环) ← 依赖 Phase 1 的图状态机
  ↓
Phase 5 (前端画布) ← 独立，可与 Phase 2-4 并行
  ↓
Phase 6 (集成联调) ← 依赖 Phase 1-5 全部完成
  ↓
Phase 7 (记忆增强) ← 依赖 Phase 6
  ↓
Phase 8 (多 Agent) ← 依赖 Phase 7
  ↓
Phase 9 (生产化) ← 依赖 Phase 8
```

**可并行的路线**:
- Phase 5 (前端) 可与 Phase 2-4 (后端核心) 并行开发
- Phase 7.1 (SQLite) 和 Phase 7.4 (评分) 可并行

---

*文档创建: 2026-04-02*
*基于: architecture.md + 12 个设计决策*
*状态: 待确认，确认后按 Phase 顺序执行*
