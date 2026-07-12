# Observe-Service Protocol: Language-Agnostic Turn Event Schema

**Version**: 1.0
**Status**: Active
**Purpose**: Unified protocol for multi-harness turn observation (agent-os-v2, claude-code, openclaw)

---

## Schema Philosophy

泛化 `canvas.events` 的 8 类事件，去除 agent-os-v2 特定耦合（GraphState），增加 `source`/`harness_id` 字段支持多 harness 接入。

---

## Core Event Fields

所有事件共享基础字段：

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string | ✓ | UUID v4，事件唯一标识 |
| `harness_type` | string | ✓ | Harness 类型：`agent-os-v2` \| `claude-code` \| `openclaw` \| `mock-*` |
| `harness_id` | string | ✓ | Harness 实例标识（如 tmux session 名、openclaw session ID） |
| `session_id` | string | ✓ | 对话会话 ID（复合键一部分） |
| `tick_id` | string | ✓ | Turn/LLM 请求标识 |
| `event_type` | string | ✓ | 事件类型（见下） |
| `data` | object | ✓ | 事件载荷（按 event_type 结构化） |
| `timestamp` | string | ✓ | ISO 8601 UTC (e.g. `2026-07-13T10:30:00.123Z`) |

**复合键约定**: `(harness_type, session_id)` 唯一标识一个 observe session。

---

## Event Types

### 1. `tick_started`

Turn 边界开始事件。

```json
{
  "event_type": "tick_started",
  "data": {
    "request": "user prompt or context summary"
  }
}
```

**Semantics**: 新 LLM 请求开始。

---

### 2. `tool_call`

工具调用事件。

```json
{
  "event_type": "tool_call",
  "data": {
    "call_id": "uuid",
    "tool_name": "Bash|Read|WebSearch|...",
    "arguments": {...}
  }
}
```

**Semantics**: 模型发起工具调用。

---

### 3. `tool_result`

工具执行结果事件。

```json
{
  "event_type": "tool_result",
  "data": {
    "call_id": "uuid (匹配 tool.call)",
    "result": "string|object (执行结果)",
    "error": "string (可选，执行失败时)"
  }
}
```

**Semantics**: 工具执行完成，返回结果给模型。

---

### 4. `tick_completed`

Turn 边界完成事件。

```json
{
  "event_type": "tick_completed",
  "data": {
    "status": "success|error",
    "response": "模型回复摘要",
    "tool_count": 3,
    "duration_ms": 1234.5
  }
}
```

**Semantics**: Turn 结束（成功或失败）。

---

### 5. `branch_created` (Optional)

分支创建事件（agent-os-v2 特有，其他 harness 可忽略）。

```json
{
  "event_type": "branch_created",
  "data": {
    "branch_id": "feature-a",
    "parent_branch_id": "main",
    "fork_tick_id": "tick-123"
  }
}
```

---

### 6. `branch_merged` (Optional)

分支合并事件（agent-os-v2 特有）。

```json
{
  "event_type": "branch_merged",
  "data": {
    "branch_id": "feature-a",
    "target_branch_id": "main",
    "merge_tick_id": "tick-456"
  }
}
```

---

## Defer Events (P2)

### `token_delta` (Token Streaming)

高频 token 流式事件，本迭代 defer。

```json
{
  "event_type": "token_delta",
  "data": {
    "token": "incremental token",
    "index": 123
  }
}
```

---

## Event Sequence Contract

**标准 turn 序列**（gateway 必须按此顺序推送）：

```
tick_started
  → [tool_call, tool_result]*
  → tick_completed
```

**多工具 turn 示例**：

```json
[
  {"event_type": "tick_started", "tick_id": "t1", "data": {"request": "列出文件"}},
  {"event_type": "tool_call", "tick_id": "t1", "data": {"tool_name": "Bash", "call_id": "c1"}},
  {"event_type": "tool_result", "tick_id": "t1", "data": {"call_id": "c1"}},
  {"event_type": "tool_call", "tick_id": "t1", "data": {"tool_name": "Read", "call_id": "c2"}},
  {"event_type": "tool_result", "tick_id": "t1", "data": {"call_id": "c2"}},
  {"event_type": "tick_completed", "tick_id": "t1", "data": {"status": "success"}}
]
```

---

## Language Binding Notes

### TypeScript (openclaw gateway)

参考实现（Python 见 `src/events.py`）：

```typescript
interface BaseEvent {
  event_id: string;
  harness_type: 'openclaw' | 'claude-code' | 'agent-os-v2';
  harness_id: string;
  session_id: string;
  tick_id: string;
  event_type: EventType;
  data: Record<string, unknown>;
  timestamp: string; // ISO 8601
}

type EventType = 'tick_started' | 'tool_call' | 'tool_result' | 'tick_completed' | 'branch_created' | 'branch_merged';
```

### JavaScript

同 TypeScript，去掉类型注解即可。

---

## Protocol Versioning

- 当前版本：`1.0`
- 后向兼容：新增 event_type 或 data 字段视为兼容
- 破坏性变更：升级主版本号（`2.0`），observe-service 支持多版本共存

---

## Transport Protocol

**WS Ingest Endpoint**: `ws://host:8002/ws/ingest`

Gateway 连接时必须注册：
- `harness_type` (query param 或 first message)
- `session_id` (query param 或 first message)

**消息格式**（单向推送）：

```json
{
  "type": "event",
  "payload": { <BaseEvent> }
}
```

observe-service 不回 ACK（fire-and-forget）。
