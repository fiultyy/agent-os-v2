# Agent OS v2 — 解构规格文档 (Deconstruct Spec)

> 版本: 1.0
> 创建: 2026-05-01
> 状态: Draft

---

## 1. 概述

### 1.1 目标

将 Agent OS 从纯 Python 架构解构为以 **Pi (TypeScript)** 为核心依赖的新架构。

### 1.2 核心约束

**Pi 零改动。** 所有 Agent OS 能力仅通过 Pi 已有的 Extension API 实现。

### 1.3 解构原则

| 原则 | 说明 |
|------|------|
| Pi 只读依赖 | 全部通过 Extension API 集成，不改 Pi 源码 |
| packages 纯数据层 | memory/observe 不 import Pi，可独立 pip install |
| Extension 松耦合 | Extension 间通过 Pi EventBus 通信，不直接 import 彼此 |
| 渐进式交付 | 分 Phase 交付，每阶段可独立运行验证 |
| 可逆性 | 每个变更可独立回退到旧架构 |

---

## 2. Pi Extension API 能力边界

Pi 已提供的 Extension API（不可扩展，只能用）：

```typescript
interface ExtensionAPI {
  // 事件订阅 — 30+ 种 lifecycle hooks
  on(event: string, handler: Function): void

  // Tool 注册
  registerTool(tool: AgentToolDefinition): void

  // 消息注入
  sendMessage(msg: Message): void
  sendUserMessage(content: string): void

  // Provider 注册
  registerProvider(name: string, config: ProviderConfig): void

  // 跨 Extension 通信（已有！）
  events: EventBus

  // 模型控制
  setModel(model: string): void
  setActiveTools(names: string[]): void

  // Session 元数据
  appendEntry(type: string, data: any): void
  setSessionName(name: string): void

  // Shell 执行
  exec(cmd: string, args: string[], opts?: ExecOptions): Promise<ExecResult>
}
```

---

## 3. 解构目录结构

```
agent-os/
├── packages/              ← 独立数据/逻辑（不依赖 Pi）
│   ├── memory/            Python: SQLiteStore + KG + Compressor + Sideline
│   └── observe/           Python: EventStore + BranchStore + TickTracker
│
├── extensions/            ← Pi Extensions（纯消费 Pi API，.ts 文件）
│   ├── frame.ts           DI: events.emit("agent-os:init", instances)
│   ├── memory.ts          registerTool: memory_recall, kg_query, memory_store
│   ├── observe.ts         on("turn_end"), on("tool_execution_*") → EventStore
│   ├── orchestrate.ts     on("agent_end") spawner, registerTool: spawn_subagent
│   ├── profile.ts         on("before_agent_start") → compile systemPrompt
│   └── tools.ts           registerTool: file, db, browser
│
├── apps/
│   └── web/               React Flow 前端
│
└── skills/
    └── agent-os.md        总控 skill（Pi 原生 SKILL.md 机制）
```

---

## 4. 依赖拓扑

```
Pi（只读，零改动）
├── pi-ai              Provider 抽象
├── pi-agent-core      Agent 循环
├── pi-coding-agent    Session + Extension API + Skill
└── pi-tui / pi-web-ui UI 层
      ↑ registerTool / on() / sendMessage / events
extensions/            ← 全部 .ts 文件，消费 Pi API
      ↑ import（纯数据/逻辑，不依赖 Pi）
packages/              ← 独立 Python/TS 库
      ↑ HTTP/WS（独立部署）
apps/web/              React Flow 前端
```

**依赖方向严格单向：Pi → extensions → packages → apps**

---

## 5. 核心机制：Pi EventBus 做 DI

不自己写 DI 容器，直接复用 Pi 已有的 `events: EventBus` 做服务定位：

```typescript
// frame.ts — 注册服务实例到 Pi EventBus
export default function(pi: ExtensionAPI) {
  pi.events.emit("agent-os:init", {
    memory: new SQLiteStore("./data/memory.db"),
    kg: new KnowledgeGraph("./data/kg.db"),
    eventStore: new EventStore("./data/events.db"),
  })
}
```

```typescript
// memory.ts — 通过 EventBus 获取依赖
export default function(pi: ExtensionAPI) {
  let store: SQLiteStore

  pi.events.on("agent-os:init", (services) => {
    store = services.memory
  })

  pi.registerTool({
    name: "memory_recall",
    label: "Memory Recall",
    description: "Search agent memory",
    parameters: Type.Object({ query: Type.String() }),
    execute: async (id, params, signal) => ({
      content: [{ type: "text", text: await store.recall(params.query) }],
      details: {},
    }),
  })
}
```

---

## 6. Agent OS 能力 → Pi Extension API 映射

| Agent OS 能力 | Pi Extension API | Extension 文件 |
|------|------|------|
| 记忆查询 | `registerTool()` | memory.ts |
| KG 查询 | `registerTool()` | memory.ts |
| 事件记录 | `on("turn_end")` + `on("tool_execution_*")` | observe.ts |
| Branch 管理 | `on("session_before_fork")` + `on("session_tree")` | observe.ts |
| Profile 编译 | `on("before_agent_start")` 修改 systemPrompt | profile.ts |
| 条件触发 Spawn | `on("agent_end")` + `sendUserMessage()` | orchestrate.ts |
| 子 Agent 管理 | `sendMessage()` + `appendEntry()` 跟踪状态 | orchestrate.ts |
| 工具注册 | `registerTool()` | tools.ts |
| 模型路由 | `registerProvider()` | 无需额外 extension |
| 跨模块通信 | `pi.events` EventBus | frame.ts |
| Skill 注入 | Pi 原生 SKILL.md 机制 | skills/agent-os.md |
| Compaction | `on("session_before_compact")` 自定义压缩 | memory.ts |
| 评分引擎 | `on("tool_result")` 收集 + `appendEntry()` 存储 | orchestrate.ts |

---

## 7. Agent OS 替代关系

### 可完全删除的模块（Pi 原生替代）

| Agent OS 模块 | Pi 替代物 |
|------|------|
| LLMClient | `pi-ai` 的 `getModel()` |
| ToolExecutor | `pi-agent-core` 的 Tool 执行循环 |
| Engine.py 主循环 | Pi session 配置器（500行→~100行） |
| EventEmitter | `session.subscribe()` + PiEventBridge |

### 保留模块（注册为 Pi AgentTool / Extension）

| 模块 | 实现方式 |
|------|------|
| MemoryService | `memory.ts` → `registerTool("memory_recall")` |
| KG | `memory.ts` → `registerTool("kg_query")` |
| ConditionalSpawner | `orchestrate.ts` → `on("agent_end")` hook |
| CommunicationBus | 协调多 Pi session（通过 Pi EventBus） |
| ConcurrencyController | 管理多 Pi session 并发 |
| BaseProfile | `profile.ts` → `on("before_agent_start")` 注入 prompt |
| CanvasEventStore | `observe.ts` → PiEventBridge 接收 Pi 事件 |
| SandboxExecutor | 待确认 Pi 有无 sandbox 能力 |

### 新增桥接层

| 桥接层 | 职责 |
|------|------|
| PiEventBridge | `session.subscribe()` → Canvas 事件流 |
| PiSessionManager | JSONL ↔ SQLite 双写，session 生命周期管理 |
| PiToolRegistry | Agent OS 能力 → `AgentTool[]` 适配 |

---

## 8. 双态使用

### 作为 Pi Extension（自动加载）

```typescript
// .pi/extensions/agent-os.ts
export { default as frame } from "./frame"
export { default as memory } from "./memory"
export { default as observe } from "./observe"
export { default as orchestrate } from "./orchestrate"
export { default as profile } from "./profile"
export { default as tools } from "./tools"
```

Pi 启动时自动加载，Extension 消费 Pi API。

### 纯 Python 独立使用（无 Pi 依赖）

```python
from agent_os.memory import SQLiteStore, KnowledgeGraph

store = SQLiteStore("./data.db")
results = store.recall("上次讨论的架构")
# 完全独立，无 Pi 依赖
```

---

## 9. 关键约束总结

| 约束 | 说明 |
|------|------|
| Pi 零改动 | 所有能力通过 Extension API |
| Pi EventBus 做 DI | 不写 DI 容器，用 `pi.events` 跨 extension |
| Extension 松耦合 | `events.on/emit` 通信，不直接 import 彼此 |
| packages 纯数据层 | 不 import Pi，可独立 pip install |
| 前端独立 | HTTP/WS 消费 observe，不依赖 Extension |

---

## 10. 待决问题

| # | 问题 | 选项 | 状态 |
|---|------|------|------|
| D1 | TS(Pi) ↔ Python(Agent OS) 跨语言桥接 | napi-rs / HTTP API / 子进程 | 待定 |
| D2 | Pi 单 session vs Agent OS 多 Agent 并发 | Session Pool 方案 | 待定 |
| D3 | Session 持久化双写一致性 | PiSessionManager JSONL↔SQLite | 待定 |

---

## 11. 架构收益

- Engine.py 从 500 行降到 ~100 行
- Agent OS 不再自己写 agent loop
- 专注于编排层和数据层
- packages 可独立发布和复用

---

*文档创建: 2026-05-01*
