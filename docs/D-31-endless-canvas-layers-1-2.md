# D-31: Endless Canvas — Layer 1 Observability + Layer 2 Control

> ⚠️ **历史文档**(写于当时, 记录当时的架构设计草案 / Endless Canvas Layer 1-2 观测+控制规划)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

**状态**: 设计草案 v2
**日期**: 2026-04-23
**目标**: 推理流的无限画布，前端观测 + 预构建控制

---

## 核心理念

> Canvas 是推理过程的 Git Log + 意识流。Layer 1 = 只读观测，Layer 2 = 预构建控制。

### 核心隐喻

- **Context Window** = LLM 的工作内存（RAM，有限）
- **Layer 1 KG** = LLM 的外存（磁盘，无限容量）
- **Tick** = 一次 LLM 请求+响应的原子单位（一等公民）
- **Tab** = UI 视口
- **Branch** = 时间线分叉（Tick 序列）
- **Session** = 数据状态（KG + Context + Staging）
- **Tab ≠ Session ≠ Branch**（三维度正交）

### Tick 模型

```
1 Tick = 原子同步单位
Tick {
  request:      LLMRequest
  response:     LLMResponse（streaming tokens）
  tool_calls:   [ToolCall → ToolResult]（0~N 个，并行或串行）
  fork:         [Branch]（工具调用产生的分支）
  tick_id:      唯一标识
  parent_tick:  父 Tick（用于追踪链条）
}
```

**为什么 Tick > Token：** Token 级太碎，Turn 级太粗。Tick 刚好对应"一次推理动作"，是 Canvas 节点渲染的最小有意义单位。Streaming tokens 是 Tick 内部渲染细节，不是独立节点。

---

## Layer 1: 观测层（只读）

### 节点类型（Tick 级）

| 节点类型 | 粒度 | 触发 |
|---------|------|------|
| `TickNode` | 1 Tick | LLM 请求+响应完成 |
| `ToolForkNode` | 工具调用分叉 | ToolCall 发起 |
| `ToolResultNode` | 工具返回 | 工具响应 |
| `ScoringSignalNode` | Scoring 信号 | ScoringEngine 触发 |
| `CommitteeVoteNode` | Committee 投票 | 投票事件 |

**注意：** `LLMToken` 不是独立节点类型。Streaming tokens 是 Tick 内部的增量渲染，不产生独立节点。

### LOD 渲染（3 级）

| Level | 显示内容 |
|-------|---------|
| **L1** | Tick 节点名 + Tool 名称 |
| **L2** | Tick 内容摘要 + Tool 参数/结果摘要 |
| **L3** | 全量 dev-debug（streaming 详情 + 元数据）|

### Canvas 布局

```
时间轴 →→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→→

Tick#1 ──────────────────→ Tick#2 ──────────────────→ Tick#3
  │                            │
  │ Tool#1                     │ Tool#2
  ▼                            ▼
ToolResult#1              ToolResult#2
  │
  │ fork
  ▼
Branch#B: Tick#B1 → Tick#B2
```

- **主路径**：水平时间线（Tick 序列向右生长）
- **工具分叉**：垂直向下分支
- **Branch 分叉**：水平偏移的平行时间线
- **新 Tick**：最右侧节点不断延伸

---

## Layer 2: 控制层（预构建请求）

### 极简节点类型

| 节点类型 | 存储 |
|---------|------|
| `TextNode` | 预输入纯文本 |
| `CommandNode` | 指令（如 `/compact`）|

**Layer 2 payload = 文本顺序拼接**，无复杂 Schema。

### Staging 操作

```
Layer 2 Staging Canvas
  ├── [TextNode] 用户预输入纯文本
  ├── [CommandNode] /compact
  └── [→ Submit] → 注入下次 Tick 请求

用户操作：
  1. 构建节点（文本 + 指令）
  2. 选择节点进入下一次请求
  3. Submit → 下次请求 payload 构建
```

### 可选：可视化组合面

| 操作 | 说明 |
|------|------|
| 拖拽 Layer 1 节点 | 引用历史上下文到 Layer 2 |
| 节点排序 | payload 顺序 |
| Submit | Staging 队列注入下次 Tick |

---

## 三维度正交模型

### Session ≠ Tab ≠ Branch

| 维度 | 含义 | 生命周期 |
|------|------|---------|
| **Session** | 数据状态（KG + Context Window + Staging）| 持久 |
| **Tab** | UI 视口（Canvas 视图）| 前端生命周期 |
| **Branch** | 时间线分叉（Tick 序列）| Session 内 |

### 关系

```
Session ──┬── Branch A ──→ Tick A1 → Tick A2 → ...
          │
          └── Branch B ──→ Tick B1 → Tick B2 → ...

Tab 1 视口 → 显示 Session 的 Branch A
Tab 2 视口 → 显示 Session 的 Branch B
Tab 3 视口 → 同时显示 Branch A + Branch B（对比视图）
```

### Branch 操作

| 操作 | 说明 |
|------|------|
| `branch/create` | 基于某 Tick 创建新 Branch |
| `branch/merge` | Branch 合并到目标（Tick 序列合并）|
| `branch/prune` | 删除 Branch |
| `tick/forward` | 将 Branch A 的 Tick 注入 Branch B 的 staging |

---

## Event Sourcing 模型

### 核心原则

```
后端推送事件，不推送状态
前端 replay 事件重建状态
```

**为什么：**
1. 天然支持 Branch（从任意 Tick replay = 创建 Branch）
2. 天然支持 LOD（replay 时按 LOD 过滤事件）
3. 天然支持回溯（回到历史任意 Tick 查看状态）
4. 前端离线恢复（重连后 replay 缺失事件）

### 事件格式

```json
{
  "event_id": "evt_xxx",
  "session_id": "sess_xxx",
  "branch_id": "br_xxx",
  "tick_id": "tick_xxx",
  "type": "tick.started | token.delta | tool.call | tool.result | tick.completed",
  "data": { ... },
  "lod": 1,
  "timestamp": "2026-04-23T00:00:00+08:00"
}
```

### WebSocket 事件协议

**后端 → 前端**

| 事件 | 说明 |
|------|------|
| `tick.started` | 新 Tick 开始 |
| `token.delta` | Streaming token 到达（批量）|
| `tool.call` | 工具调用发起 |
| `tool.result` | 工具返回 |
| `tick.completed` | Tick 完成 |
| `branch.created` | 新分支创建 |
| `branch.merged` | 分支合并 |
| `scoring.signal` | Scoring 信号触发 |
| `committee.vote` | Committee 投票 |

**前端 → 后端**

| 事件 | 说明 |
|------|------|
| `session.subscribe` | 订阅 session 流 |
| `branch.create` | 基于 Tick 创建分支 |
| `branch.merge` | 合并分支 |
| `branch.prune` | 删除分支 |
| `layer2.add` | Layer 2 新增节点 |
| `layer2.submit` | 提交 Layer 2 到下次 Tick |
| `session.chat` | 直接发送 chat（绕过 Layer 2）|

---

## KG 共享原则

### 无冲突

- 所有 Branch 共享同一 Layer 1 KG
- 写入 = 追加节点，无覆盖/冲突
- LLM 通过 KG 工具自行去重/查询

### Context Window 溢出 → KG 外存

```
Context Window（LLM RAM，有限）
     ↓ 溢出
Layer 1 KG（LLM Disk，无限）
     ↓ 查询
LLM 通过工具重建上下文
```

---

## 技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| 前端框架 | React 18 + TypeScript | |
| 画布引擎 | React Flow | 节点编辑器 + 虚拟视口 |
| 状态管理 | Zustand | Tab/Branch 状态 |
| WebSocket | 已有 Gateway 扩展 | Event Sourcing 事件协议 |
| 分支持久化 | SQLite（Session 表）| Branch metadata + KG 指针 |
| 实时状态 | WebSocket 推送 | Event replay |

---

## 实施优先级

### P0：Layer 1 基础

| 组件 | 说明 |
|------|------|
| Event Sourcing 协议 | WebSocket 事件 + tick_id/branch_id |
| React Flow 渲染 | Tick 节点 + 时间轴生长 |
| Tab 分支管理 | 创建/切换/关闭 Tab |
| LOD 渲染 | 3 级精度切换 |

### P1：Layer 2 预构建

| 组件 | 说明 |
|------|------|
| Layer 2 极简节点 | TextNode + CommandNode |
| Staging submit | 触发后端 Tick |
| Branch 管理 | create/merge/prune |

### P2：Branch 并行

| 组件 | 说明 |
|------|------|
| 多 Branch 并行 | Tab 内多分支同步推进 |
| Cross-tab 操作 | Branch A → Branch B 注入 |
| KG 写锁 | 并行写入冲突防护 |

### P3：高级观测

| 组件 | 说明 |
|------|------|
| ScoringOverlay | 蝴蝶信号网络叠加 |
| Committee 投票可视化 | 节点 + 事件 |
| Event replay | 历史 Tick 回溯 |

---

*设计草案 v2，完成，等待实施*
