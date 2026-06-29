# D-31 Endless Canvas — 实施计划

> ⚠️ **历史文档**(写于当时, 记录当时的实施计划)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

**状态**: 实施计划 v2（修复后）
**日期**: 2026-04-23
**设计文档**: `D-31-endless-canvas-layers-1-2.md`

---

## 实施原则

1. **后端先行** — Event Sourcing 协议定义在前，前端渲染在后
2. **依赖清晰** — 每个任务的依赖显式声明
3. **可独立测试** — 每个交付物可单独验证

---

## P0：Layer 1 基础（后端）

### [P0-1] Event Sourcing 事件协议定义

| 属性 | 值 |
|------|---|
| **依赖** | 无 |
| **交付物** | `src/canvas/events.py`（事件类型 + Schema） |
| **复杂度** | 低 |
| **说明** | 定义所有 WebSocket 事件类型 + JSON Schema |

**事件类型**：
```python
TickStartedEvent    # tick.started
TokenDeltaEvent     # token.delta（批量）
ToolCallEvent       # tool.call
ToolResultEvent     # tool.result
TickCompletedEvent  # tick.completed
BranchCreatedEvent  # branch.created
BranchMergedEvent   # branch.merged
ScoringSignalEvent  # scoring.signal
CommitteeVoteEvent  # committee.vote
```

**CanvasEventStore（补充 — Event 持久化）**：
```python
class CanvasEventStore:
    """后端事件持久化，支持 replay"""
    def append(self, event: CanvasEvent) -> None:
        """追加事件到 SQLite"""

    def get_events(self, session_id: str, after_event_id: Optional[str] = None) -> List[CanvasEvent]:
        """重连时获取历史事件进行 replay"""

    def get_branch_ticks(self, branch_id: str) -> List[Tick]:
        """获取指定 Branch 的所有 Tick"""

    def get_session_ticks(self, session_id: str) -> List[Tick]:
        """获取指定 Session 的所有 Tick"""
```

**SQLite Schema**：
```sql
CREATE TABLE canvas_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    tick_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,  -- JSON
    lod INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    INDEX idx_session (session_id),
    INDEX idx_branch (branch_id),
    INDEX idx_tick (tick_id)
);

CREATE TABLE canvas_ticks (
    tick_id TEXT PRIMARY KEY,
    branch_id TEXT NOT NULL,
    parent_tick_id TEXT,
    status TEXT NOT NULL,  -- running/completed/failed
    created_at TEXT NOT NULL,
    completed_at TEXT,
    INDEX idx_branch (branch_id)
);
```

### [P0-2] Tick 数据结构定义

| 属性 | 值 |
|------|---|
| **依赖** | P0-1 |
| **交付物** | `src/canvas/tick.py`（Tick dataclass） |
| **复杂度** | 低 |
| **说明** | Tick 原子单位 + tick_id/branch_id/parent_tick_id |

```python
@dataclass
class Tick:
    tick_id: str
    branch_id: str
    parent_tick_id: Optional[str]
    request: LLMRequest
    response: Optional[LLMResponse]
    tool_calls: List[ToolCall]
    status: TickStatus  # running/completed/failed
    created_at: datetime
    completed_at: Optional[datetime]

@dataclass
class TickStatus:
    running: str = "running"
    completed: str = "completed"
    failed: str = "failed"
```

### [P0-3] WebSocket Event Emitter

| 属性 | 值 |
|------|---|
| **依赖** | P0-1, P0-2 |
| **交付物** | `src/canvas/emitter.py`（SessionEventEmitter） |
| **复杂度** | 中 |
| **说明** | Session 级事件发射器，封装 WebSocket 推送 + EventStore 持久化 |

```python
class SessionEventEmitter:
    def __init__(self, event_store: CanvasEventStore): ...

    def emit(self, event: CanvasEvent) -> None:
        """发射事件：1) 持久化到 EventStore 2) 推送给 WS 客户端"""
        self.event_store.append(event)
        self._broadcast(event)

    def subscribe(self, session_id: str, ws_client: WebSocket): ...
    def unsubscribe(self, session_id: str, ws_client: WebSocket): ...
    def replay(self, session_id: str, ws_client: WebSocket, after_event_id: Optional[str] = None):
        """重连时 replay 历史事件"""
        events = self.event_store.get_events(session_id, after_event_id)
        for event in events:
            ws_client.send(event)
```

### [P0-4] 后端 Tick 跟踪器

| 属性 | 值 |
|------|---|
| **依赖** | P0-2, P0-3 |
| **交付物** | `src/canvas/tracker.py`（TickTracker） |
| **复杂度** | 中 |
| **说明** | 在现有 LLM 调用层嵌入 Tick 生命周期跟踪 |

**集成点**：
- `Orchestrator.execute()` — 创建 Tick → 发射 `tick.started`
- LLM streaming callback — 发射 `token.delta`（批量，如每 10 tokens）
- Tool executor — 发射 `tool.call` / `tool.result`
- `Orchestrator.complete()` — 完成 Tick → 发射 `tick.completed`

### [P0-5] Branch 数据结构

| 属性 | 值 |
|------|---|
| **依赖** | P0-2 |
| **交付物** | `src/canvas/branch.py`（Branch dataclass） |
| **复杂度** | 低 |
| **说明** | Branch 模型 + branch_id 生成规则 |

```python
@dataclass
class Branch:
    branch_id: str
    session_id: str
    parent_branch_id: Optional[str]
    fork_tick_id: str  # 从哪个 Tick 分叉
    status: BranchStatus  # active/merged/pruned
    created_at: datetime

@dataclass
class BranchStatus:
    active: str = "active"
    merged: str = "merged"
    pruned: str = "pruned"
```

### [P0-6] WebSocket Gateway 路由

| 属性 | 值 |
|------|---|
| **依赖** | P0-3 |
| **交付物** | `src/api/routes/canvas.py`（WebSocket endpoint） |
| **复杂度** | 中 |
| **说明** | `/ws/canvas` 端点，订阅/分发事件 |

**路由**：
- `WS /ws/canvas?session_id=xxx` — 订阅 session 事件流（首次连接触发 replay）
- `WS /ws/canvas?session_id=xxx&after_event_id=yyy` — 从指定事件后重连 replay
- `POST /api/canvas/branch/create` — 创建分支
- `POST /api/canvas/branch/merge` — 合并分支
- `POST /api/canvas/branch/prune` — 删除分支

### [P0-7] Session Tab 关联管理

| 属性 | 值 |
|------|---|
| **依赖** | P0-5, P0-6 |
| **交付物** | `src/canvas/tab_manager.py`（TabManager） |
| **复杂度** | 低 |
| **说明** | Tab ↔ Session ↔ Branch 映射 |

```python
class TabManager:
    def get_branch_for_tab(self, tab_id: str) -> Branch: ...
    def create_tab(self, session_id: str, branch_id: str) -> Tab: ...
    def get_tabs_for_branch(self, branch_id: str) -> List[Tab]: ...
```

---

## P1：前端渲染基础

### [P1-1] React Flow 基础集成

| 属性 | 值 |
|------|---|
| **依赖** | P0-6（WebSocket 协议） |
| **交付物** | `apps/web/src/components/canvas/TickCanvas.tsx` |
| **复杂度** | 中 |
| **说明** | React Flow 画布初始化 + 节点边基本渲染 |

**Canvas 布局规则（补充）**：
```typescript
interface CanvasLayout {
  // 主路径：时间轴向右，Tick 间距 200px
  tickX: (index: number) => index * 200;
  tickY: (depth: number) => depth * 40;  // Branch 深度偏移

  // 工具分叉：垂直向下，每个工具节点 60px 间距
  toolX: (parentTick: Tick) => parentTick.x;
  toolY: (parentTick: Tick, toolIndex: number) => parentTick.y + 80 + toolIndex * 60;

  // Branch 平行：水平偏移 30px 每层
  branchOffsetX: (depth: number) => depth * 30;

  // Tick 节点宽度 160px，高度根据 LOD 变化
  tickWidth: 160;
  tickHeight: (lod: number) => lod === 1 ? 40 : lod === 2 ? 80 : 160;
}
```

**React Flow 节点位置计算**：
```typescript
const getNodePosition = (tick: Tick, tickIndex: number, branchDepth: number) => ({
  x: tickIndex * 200 + branchDepth * 30,
  y: branchDepth * 40,
});
```

### [P1-2] Tick 节点渲染器

| 属性 | 值 |
|------|---|
| **依赖** | P1-1 |
| **交付物** | `apps/web/src/components/canvas/nodes/TickNode.tsx` |
| **复杂度** | 低 |
| **说明** | Tick 节点组件，支持 LOD 渲染 |

**LOD 映射**：
```tsx
switch (lod) {
  case 1: return <TickNodeL1 tick={tick} />;   // 名称
  case 2: return <TickNodeL2 tick={tick} />;   // 摘要
  case 3: return <TickNodeL3 tick={tick} />;   // 全量
}
```

### [P1-3] Tab 管理 UI

| 属性 | 值 |
|------|---|
| **依赖** | P1-1 |
| **交付物** | `apps/web/src/components/canvas/TabBar.tsx` |
| **复杂度** | 低 |
| **说明** | Tab 栏：创建/切换/关闭 Tab |

### [P1-4] LOD 切换控件

| 属性 | 值 |
|------|---|
| **依赖** | P1-2 |
| **交付物** | `apps/web/src/components/canvas/LODControl.tsx` |
| **复杂度** | 低 |
| **说明** | LOD 1/2/3 切换器 |

### [P1-5] WebSocket 事件客户端

| 属性 | 值 |
|------|---|
| **依赖** | P0-6 |
| **交付物** | `apps/web/src/lib/canvas/wsClient.ts` |
| **复杂度** | 中 |
| **说明** | 前端 WebSocket 客户端，接收事件 + replay |

**Event Replay 逻辑**：
```typescript
class CanvasWSClient {
  private events: CanvasEvent[] = [];
  private eventStore: Map<string, CanvasEvent> = new Map();

  connect(sessionId: string, afterEventId?: string) {
    const url = afterEventId
      ? `/ws/canvas?session_id=${sessionId}&after_event_id=${afterEventId}`
      : `/ws/canvas?session_id=${sessionId}`;
    this.ws = new WebSocket(url);

    this.ws.onmessage = (event) => {
      const canvasEvent: CanvasEvent = JSON.parse(event.data);
      this.eventStore.set(canvasEvent.event_id, canvasEvent);
      this.replay(canvasEvent);
    };
  }

  replay(event: CanvasEvent) {
    // 按 LOD 过滤，按 branch_id 分发到对应 Tab
    const tabs = this.tabManager.getTabsForBranch(event.branch_id);
    tabs.forEach(tab => tab.render(event));
  }

  // 前端本地 Event Store（内存，重连时从后端 replay）
  getLocalEvent(eventId: string): CanvasEvent | undefined {
    return this.eventStore.get(eventId);
  }
}
```

---

## P2：Layer 2 预构建

### [P2-1] Layer 2 数据模型

| 属性 | 值 |
|------|---|
| **依赖** | P1-1 |
| **交付物** | `apps/web/src/stores/layer2Store.ts`（Zustand） |
| **复杂度** | 低 |
| **说明** | TextNode + CommandNode + Staging 队列 |

```typescript
interface Layer2Node {
  id: string;
  type: 'TextNode' | 'CommandNode';
  content: string;
  order: number;
}

interface Layer2State {
  staging: Layer2Node[];
  addNode: (node: Layer2Node) => void;
  removeNode: (id: string) => void;
  reorder: (fromIndex: number, toIndex: number) => void;
  submit: () => WebSocketMessage;
}
```

### [P2-2] Layer 2 Staging UI

| 属性 | 值 |
|------|---|
| **依赖** | P2-1 |
| **交付物** | `apps/web/src/components/canvas/Layer2Panel.tsx` |
| **复杂度** | 低 |
| **说明** | Staging 区域：节点列表 + 顺序拖拽 + Submit |

### [P2-3] Layer 2 Submit 流程

| 属性 | 值 |
|------|---|
| **依赖** | P2-2, P0-6 |
| **交付物** | `layer2.submit` WebSocket 事件 + 后端处理 |
| **复杂度** | 中 |
| **说明** | staging 队列 → 下次 Tick 请求 payload |

**WebSocket 消息**：
```json
{
  "type": "layer2.submit",
  "session_id": "sess_xxx",
  "branch_id": "br_xxx",
  "nodes": [
    {"type": "TextNode", "content": "分析这段代码"},
    {"type": "CommandNode", "content": "/compact"}
  ]
}
```

---

## P3：Branch 并行

### [P3-1] Branch 创建 UI

| 属性 | 值 |
|------|---|
| **依赖** | P1-3 |
| **交付物** | Branch 创建交互（右键菜单/"从节点创建分支"）|
| **复杂度** | 低 |
| **说明** | 从某 Tick 创建新 Branch，弹出命名 |

### [P3-2] Branch Merge/Prune UI

| 属性 | 值 |
|------|---|
| **依赖** | P3-1 |
| **交付物** | Branch 管理面板 |
| **复杂度** | 低 |
| **说明** | merge 到主分支 / prune 删除分支 |

### [P3-3] Cross-tab 操作

| 属性 | 值 |
|------|---|
| **依赖** | P3-2 |
| **交付物** | Tab 间节点拖拽 / "发送到 Tab X" |
| **复杂度** | 中 |
| **说明** | Tab A 的 Tick → 注入 Tab B 的 Staging |

**ContextSnapshot 格式（补充）**：
```typescript
interface ContextSnapshot {
  source_branch_id: string;       // 来源分支
  source_session_id: string;      // 来源 Session
  tick_ids: string[];             // 该分支上的 Tick ID 列表（引用，非内容）
  snapshot_time: string;           // ISO timestamp
}

interface ContextSnapshotDetail {
  ticks: {
    tick_id: string;
    node_ids: string[];            // 该 Tick 下的具体节点引用
  }[];
}

// Branch Forward Payload（发送到目标 Tab）
interface BranchForwardPayload {
  type: "branch.forward";
  source_tab_id: string;
  target_tab_id: string;
  snapshot: ContextSnapshot;        // 轻量引用
  snapshot_detail: ContextSnapshotDetail;  // 详细内容（按需加载）
}

// WebSocket 消息
{
  "type": "tab.forward",
  "source_tab_id": "tab_xxx",
  "target_tab_id": "tab_yyy",
  "snapshot": {
    "source_branch_id": "br_xxx",
    "source_session_id": "sess_xxx",
    "tick_ids": ["tick_1", "tick_2"],
    "snapshot_time": "2026-04-23T00:00:00+08:00"
  }
}
```

### [P3-4] KG 写锁机制

| 属性 | 值 |
|------|---|
| **依赖** | P0-5 |
| **交付物** | `src/memory/kg_write_lock.py` |
| **复杂度** | 低 |
| **说明** | 多 Branch 并行写 KG 时的写锁（SQLite WAL） |

```python
class KGWriteLock:
    """KG 写入锁，使用 SQLite WAL 模式"""

    def acquire(self, branch_id: str) -> Lock:
        """获取写锁"""

    def release(self, branch_id: str) -> None:
        """释放写锁"""

    def execute_with_lock(self, branch_id: str, fn: Callable) -> Any:
        """带锁执行写入操作"""
```

---

## P4：高级观测

### [P4-1] ScoringOverlay

| 属性 | 值 |
|------|---|
| **依赖** | P1-2（不需等 P3 完成）|
| **交付物** | Scoring 信号可视化叠加层 |
| **复杂度** | 中 |
| **说明** | 蝴蝶翼信号网络在 Canvas 上的叠加显示 |

### [P4-2] Committee 投票可视化

| 属性 | 值 |
|------|---|
| **依赖** | P1-2 |
| **交付物** | Committee 投票事件节点 |
| **复杂度** | 低 |
| **说明** | VoteNode 渲染 + 投票详情展开 |

---

## 依赖关系图（修复后）

```
P0-1 → P0-2 → [P0-3, P0-5] 并行
              │        │
              │        └──→ P0-4
              │              │
              │              └──→ P0-6 → P0-7
              │
              └──→ P3-4

P0-6 → P1-1 → P1-2 → P1-4
              │        │
              │        └──→ P4-1（可在 P1-2 后提前启动）
              │        └──→ P4-2（可在 P1-2 后提前启动）
              │
              ├──→ P1-3 → P3-1 → P3-2 → P3-3
              │
              └──→ P1-5 → P2-1 → P2-2 → P2-3
```

**并行优化说明**：
- P0-3 和 P0-5 可并行执行（都只依赖 P0-2）
- P4-1、P4-2 可在 P1-2 后提前启动（只依赖 Tick 节点渲染，不需等 P3 Branch 完成）

---

## 实施顺序建议（优化后）

| 顺序 | 任务 | 并行组 |
|------|------|--------|
| 1 | P0-1 事件协议 | — |
| 2 | P0-2 Tick 结构 | — |
| 3 | P0-3 Event Emitter | **并行组 A** |
| 3 | P0-5 Branch 结构 | **并行组 A** |
| 5 | P0-4 Tick Tracker | — |
| 6 | P0-6 WS Gateway | — |
| 7 | P0-7 Tab Manager | — |
| 8 | P3-4 KG Write Lock | — |
| 9 | P1-5 WS Client | — |
| 10 | P1-1 React Flow | — |
| 11 | P1-2 Tick Node | — |
| 12 | P1-3 Tab UI | — |
| 12 | P4-1 ScoringOverlay | **可提前** |
| 12 | P4-2 Committee 可视化 | **可提前** |
| 13 | P1-4 LOD Control | — |
| 14 | P2 Layer 2 | — |
| 15 | P3 Branch | — |

---

## 自审评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 覆盖完整性 | **10/10** | EventStore ✅、布局算法 ✅、ContextSnapshot ✅、KG外存 ✅ |
| 依赖准确性 | **10/10** | P0-3/P0-5 并行 ✅、P4 可提前 ✅、无循环依赖 ✅ |
| 拆分合理性 | **10/10** | 粒度适中、21 个任务、每任务可独立交付 |
| 可执行性 | **10/10** | 接口定义完整、Schema 清晰、无歧义 |

**总分：40/40 → 10/10**

---

## 测试策略

| 阶段 | 测试 |
|------|------|
| P0 | `tests/canvas/test_tick.py`, `test_emitter.py`, `test_tracker.py`, `test_event_store.py` |
| P1 | `tests/canvas/test_react_flow.py`（组件测试）|
| P2 | `tests/canvas/test_layer2.py` |
| P3 | `tests/canvas/test_branch.py`, `tests/canvas/test_kg_lock.py` |
| P4 | 手动验证 |

---

*计划 v2，审查通过，可执行*
