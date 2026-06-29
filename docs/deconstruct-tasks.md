# Agent OS 解构任务清单

> ⚠️ **历史文档**(写于当时, 记录当时的实现计划 / 解构任务清单)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

> 将 Python Agent OS 逐步重构为以 Pi (TypeScript) 为核心依赖的新架构。
> 创建时间: 2026-05-01

---

## Phase 0: 准备

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 | 状态 |
|----|------|----------|--------|----------|------|
| P0-1 | Pi mono repo 分析 | 无 | M | 输出 Pi 核心模块清单（agent-core, ai, session, tools）及导出 API 列表 | ✅ |
| P0-2 | Agent OS 依赖矩阵 | P0-1 | M | 输出每个 Agent OS 模块对 Pi 的映射关系（替代/保留/桥接） | ✅ |
| P0-3 | 创建 worktree | 无 | S | `agent-os-v2` worktree 创建成功，CI 绿灯 | ✅ |
| P0-4 | 解构规格文档 | P0-1, P0-2 | L | spec + tasks + progress 三文档完成，commit `92d9421` | ✅ |

---

## Phase 1: @agent-os/frame

> 定义所有跨 package 共享的 TypeScript 接口和类型，作为 Frame Scope 基础。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P1-1 | 定义 `IMemoryStore` 接口 | P0-4 | M | 接口覆盖 get/set/delete/search/clear，含 TypeScript 泛型签名和 JSDoc |
| P1-2 | 定义 `IKnowledgeGraph` 接口 | P0-4 | M | 接口覆盖 entity/relation CRUD + 图遍历(query path/subgraph)，对齐现有 SQLite KG 操作 |
| P1-3 | 定义 `IGraphEngine` 接口 | P0-4 | S | 接口覆盖 compile/run/reset，对接 Pi EventBus 生命周期 |
| P1-4 | 定义 `IEventSink` 接口 | P0-4 | S | 接口覆盖 emit/subscribe/dispose，与 Pi session.subscribe 兼容 |
| P1-5 | 定义共享类型: Tick, Branch, Event, AgentBlock | P0-4 | L | 完整 TypeScript type 定义，从 Python dataclass 逐字段移植，含单元类型测试 |
| P1-6 | 实现 FrameContext DI 容器 | P1-1~P1-5 | L | 基于 Pi EventBus，支持 register/resolve/lifecycle，所有 frame 接口可注入，含基础测试 |

---

## Phase 2: 独立 Packages

> 将 Python 核心数据层提取为独立 TypeScript/Python 包，不依赖 Pi。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P2-1 | `@agent-os/memory` — SQLiteStore 移植 | P1-1, P1-5 | L | 从 Python `sqlite_store.py` 移植为 TS，实现 IMemoryStore 接口，原有单元测试等价通过 |
| P2-2 | `@agent-os/memory` — KnowledgeGraph 移植 | P1-2 | L | 从 Python SQLite KG 移植为 TS，实现 IKnowledgeGraph 接口，entities+relations 双表 + 递归 CTE |
| P2-3 | `@agent-os/memory` — Compressor 移植 | P1-1 | M | 从 Python `compressor.py` 移植为 TS，11→3 压缩逻辑保留，含测试 |
| P2-4 | `@agent-os/memory` — Sideline 移植 | P1-1, P1-2 | M | 从 Python Sideline 管道移植为 TS，JSONL→ActionUnit→KG 异步管道，含测试 |
| P2-5 | `@agent-os/memory` — Python binding 层 | P2-1~P2-4 | M | 为 TS memory 包提供 Python FFI (napi-rs 或 HTTP)，Python 端可调用 IMemoryStore/IKG |
| P2-6 | `@agent-os/observe` — EventStore 移植 | P1-4, P1-5 | M | 从 Python `event_store.py` 移植为 TS，实现 IEventSink，event sourcing 模式 |
| P2-7 | `@agent-os/observe` — BranchStore 移植 | P1-5 | M | 从 Python `branch_store.py` 移植为 TS，SQLite 持久化，CRUD + merge/prune |
| P2-8 | `@agent-os/observe` — TickTracker 移植 | P1-5 | S | 从 Python `tick.py` 移植为 TS，tick 计数 + 分支追踪 |
| P2-9 | `@agent-os/tools` — FileTool 移植 | P0-4 | S | 从 Python FileTool 移植为 Pi AgentTool 格式，注册到 Pi ToolRegistry |
| P2-10 | `@agent-os/tools` — DBTool 移植 | P0-4 | S | 从 Python DBTool 移植为 Pi AgentTool 格式 |
| P2-11 | `@agent-os/tools` — BrowserTool 移植 | P0-4 | M | 从 Python BrowserTool 移植为 Pi AgentTool 格式，需处理浏览器进程管理 |

---

## Phase 3: Pi Extensions

> 将 Agent OS 能力注册为 Pi extension，实现 PiEventBridge 和 PiSessionManager。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P3-1 | `frame.ts` extension | P1-6 | M | 注册 FrameContext 到 Pi extension system，Pi session 启动时自动初始化 DI |
| P3-2 | `memory.ts` extension | P2-1~P2-5 | M | 注册 `memory_recall` / `kg_query` 为 Pi AgentTool，Pi agent 可直接调用 |
| P3-3 | `observe.ts` extension | P2-6~P2-8 | M | 实现 PiEventBridge：Pi session.subscribe → Canvas 事件流，前端可订阅 |
| P3-4 | `orchestrate.ts` extension | P3-1 | M | Hook Pi session lifecycle (on_start/on_tool/on_end)，触发 ConditionalSpawner / ConcurrencyController |
| P3-5 | `profile.ts` extension | P3-1 | M | BaseProfile.compile() 输出注入 Pi system prompt，LayerCompiler 集成 |
| P3-6 | `tools.ts` extension | P2-9~P2-11 | S | 注册 FileTool/DBTool/BrowserTool 到 Pi ToolRegistry |
| P3-7 | PiSessionManager | P3-1, P3-3 | L | 多 Pi session 管理 (create/pool/destroy)，JSONL ↔ SQLite 双写，session 生命周期钩子 |
| P3-8 | Python ↔ Pi 桥接层 | P3-7 | L | Python 端通过 napi-rs/HTTP 调用 Pi session，Session Pool 连接管理，错误传播 |

---

## Phase 4: Frame Scope Packages

> 高层编排能力，依赖 Phase 2-3 的基础组件。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P4-1 | `@agent-os/orchestrate` — GraphStateMachine | P1-3, P3-4 | L | 从 Python 移植图状态机，节点执行 + 状态转换，对接 Pi EventBus |
| P4-2 | `@agent-os/orchestrate` — MetaAgentNode | P4-1, P3-7 | L | ConditionalSpawner + Sandbox 集成，管理子 Pi session 创建/监控/销毁 |
| P4-3 | `@agent-os/orchestrate` — ScoringEngine | P1-2, P3-2 | M | ButterflySignalPolicy 评分，KG 图结构特征提取 (degree/pagerank/cluster_coef) |
| P4-4 | `@agent-os/orchestrate` — ConditionalSpawner | P4-2, P4-3 | M | 基于评分条件触发子 agent 创建，对接 PiSessionManager session pool |
| P4-5 | `@agent-os/profile` — LayerCompiler | P3-5 | L | 三层 Profile (System/User/Tool) 编译，注入 Pi system prompt，含 ScoringCalibration |
| P4-6 | `@agent-os/profile` — PluginRegistry | P4-5 | M | Agent 能力插件注册/发现/生命周期，与 Pi extension system 双向同步 |

---

## Phase 5: 集成测试

> 端到端验证新旧架构兼容性。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P5-1 | Pi + 单 extension 集成测试 | P3-1~P3-6 | M | Pi 启动 + 逐个 extension 加载，验证注册的 Tool/Sink 正确工作 |
| P5-2 | Pi + 全 extension 集成测试 | P5-1, P3-7 | L | Pi 完整启动，所有 extension 同时加载，memory/observe/orchestrate 协同工作 |
| P5-3 | 纯 Python 独立运行验证 | P2-1~P2-8 | M | Python 端通过桥接层独立运行（不依赖 Pi），原有 E2E 测试等价通过 |
| P5-4 | 前端 WS 集成测试 | P5-2, P3-3 | M | 前端通过 WebSocket 接收 Canvas 事件，Branch/Tick CRUD 全链路通过 |
| P5-5 | 多 Agent 并发场景测试 | P5-2, P4-4 | L | 3+ 并发 Pi session，ConditionalSpawner 自动创建/销毁，KG 正确合并 |
| P5-6 | 性能基准对比 | P5-2, P5-3 | M | 对比新架构 vs 旧架构在 memory recall / KG query / event throughput 的性能 |

---

## Phase 6: 清理

> 删除被 Pi 替代的旧模块，更新文档和 CI。

| ID | 描述 | 前置依赖 | 复杂度 | 验收标准 |
|----|------|----------|--------|----------|
| P6-1 | 删除 LLMClient | P5-2 | S | 代码移除，无编译错误，相关测试更新 |
| P6-2 | 删除 ToolExecutor | P5-2 | S | 代码移除，Pi agent-core Tool 循环替代验证 |
| P6-3 | 删除 Engine.py 主循环 | P5-2 | M | 500 行→~100 行 PiSessionManager，功能等价 |
| P6-4 | 删除 EventEmitter | P5-2 | S | 代码移除，PiEventBridge 完全替代 |
| P6-5 | 更新架构文档 | P6-1~P6-4 | M | architecture.md / STATUS.md 反映新架构，模块依赖图更新 |
| P6-6 | 更新 CI/CD | P5-5, P6-5 | M | CI 覆盖 TS 构建 + Python 桥接测试 + 集成测试，旧 Python 单测套件保留为回归 |
| P6-7 | 迁移指南 | P6-5 | M | 输出从 v1 (Python) 到 v2 (Pi+TS) 的迁移文档，含 breaking changes |

---

## 依赖关系总览

```
P0-4 ──→ P1-1~P1-6 ──→ P2-* ──→ P3-* ──→ P4-* ──→ P5-* ──→ P6-*
```

关键路径: P0-4 → P1-6 → P2-1 → P3-7 → P4-2 → P5-2 → P6-3

---

## 统计

| Phase | 任务数 | S | M | L | 总复杂度 |
|-------|--------|---|---|---|----------|
| Phase 0 | 4 | 1 | 1 | 2 | 6 |
| Phase 1 | 6 | 1 | 2 | 3 | 14 |
| Phase 2 | 11 | 3 | 5 | 3 | 26 |
| Phase 3 | 8 | 1 | 3 | 4 | 24 |
| Phase 4 | 6 | 0 | 3 | 3 | 21 |
| Phase 5 | 6 | 0 | 3 | 3 | 21 |
| Phase 6 | 7 | 3 | 3 | 1 | 14 |
| **合计** | **48** | **9** | **20** | **19** | **126** |
