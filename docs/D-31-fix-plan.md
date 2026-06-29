# D-31 Endless Canvas 修复计划

> ⚠️ **历史文档**(写于当时, 记录当时的实现计划/修复排期)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

**状态**: 修复计划
**日期**: 2026-04-23
**基于**: 代码审查评分 4.5/10，6 CRITICAL + 14 WARNING

---

## 修复总览

| 优先级 | 数量 | 预估工时 |
|--------|------|----------|
| CRITICAL | 6 | 6-8h |
| WARNING | 14 | 8-10h |
| 架构对齐 | 1 | 4-6h |
| **合计** | **21** | **18-24h** |

---

## CRITICAL 阻塞性问题（6 个）

### C-1: tracker.py 同步调用 async emit() — 事件管道完全失效

**根因分析**:
- `TickTracker` 的所有方法（`start_tick`, `record_token`, `record_tool_call`, `record_tool_result`, `complete_tick`, `fail_tick`）都是**同步方法**
- 这些方法内部调用 `self._emitter.emit(event)`，但 `SessionEventEmitter.emit()` 是 `async def`
- 同步调用 async 函数返回一个 coroutine 对象，该 coroutine **从未被 await**
- 结果：事件**永远不会**被持久化到 EventStore，也永远不会被广播到 WebSocket 客户端
- **整个事件管道从根上就是断的**，后端所有事件全部丢失

**修复方案**:

方案 A（推荐）— 将 TickTracker 改为 async：
- `TickTracker` 所有公共方法改为 `async def`
- 所有 `self._emitter.emit(event)` 改为 `await self._emitter.emit(event)`
- 上层调用方（Orchestrator）也必须在 async context 中调用
- 优点：语义正确，事件保证执行；缺点：需修改调用链

方案 B — 使用 fire-and-forget：
- 保持 TickTracker 同步，但通过 `asyncio.ensure_future()` 或事件循环调度 emit
- 缺点：丢失错误信息，不保证执行顺序

**涉及文件**:
- `services/orchestrator/src/canvas/tracker.py` — 所有 6 个方法改为 async
- `services/orchestrator/src/canvas/emitter.py` — 无修改（已经是 async）
- 上层调用方（Orchestrator/LLM 调用层）— 需确保在 async context 中调用 tracker

**验收标准**:
1. `tracker.start_tick()` 调用后，`canvas_events` 表中出现 `tick.started` 记录
2. WebSocket 客户端能收到实时事件
3. `tracker.complete_tick()` 后，事件状态为 `completed`

---

### C-2: WebSocket 无任何身份验证

**根因分析**:
- `canvas.py:75` — `await ws.accept()` 无条件接受所有连接
- `session_id` 由客户端通过 Query 参数传入，无验证
- 任何人可连接任意 `session_id`，接收完整事件流（可能包含敏感对话内容、工具调用参数等）
- 潜在风险：信息泄露、会话劫持

**修复方案**:
1. 在 `ws.accept()` 之前验证请求：
   - 检查 Cookie 或 Authorization header 中的 JWT token
   - 验证 `session_id` 是否属于该用户
2. 增加来源校验（Origin header 检查）
3. 添加连接速率限制（防止 WS 连接耗尽攻击）

```python
@router.websocket("/ws/canvas")
async def canvas_websocket(
    ws: WebSocket,
    session_id: str = Query("default"),
    token: str = Query(""),  # 或从 cookie/header 获取
) -> None:
    # 验证 token
    user = await verify_token(token)
    if not user:
        await ws.close(code=4001, reason="Unauthorized")
        return
    # 验证 session 归属
    if not await verify_session_access(user, session_id):
        await ws.close(code=4003, reason="Forbidden")
        return
    await ws.accept()
    ...
```

**涉及文件**:
- `services/orchestrator/src/api/routes/canvas.py` — WS 端点添加认证中间件
- 可能需要新增 `services/orchestrator/src/api/auth.py` — 认证工具函数

**验收标准**:
1. 无 token 连接被拒绝（close code 4001）
2. 无效 token 连接被拒绝
3. 合法 token 可正常连接并接收事件
4. Origin 校验拒绝跨域 WebSocket 连接

---

### C-3: TickCanvas.tsx 节点位置不区分 branch — 所有 tick Y 坐标固定为 100

**根因分析**:
- `TickCanvas.tsx:31` — `position: { x: DEFAULT_LAYOUT.tickX * idx, y: DEFAULT_LAYOUT.tickY }`
- `DEFAULT_LAYOUT.tickY = 100`，所有 tick 的 Y 坐标恒为 100
- `idxMap` 按 `branch_id` 计数，但 Y 坐标没有根据 branch 深度偏移
- 不同 branch 的 tick 节点**完全重叠**在 Y=100 这条线上，无法区分

**修复方案**:

1. 建立 branch → 深度映射：
```typescript
const branchDepthMap: Record<string, number> = {};
let depthCounter = 0;
ticks.forEach(tick => {
  if (!(tick.branch_id in branchDepthMap)) {
    branchDepthMap[tick.branch_id] = depthCounter++;
  }
});
```

2. 节点位置按 branch 深度偏移：
```typescript
const depth = branchDepthMap[tick.branch_id];
const x = DEFAULT_LAYOUT.tickX * idx + DEFAULT_LAYOUT.branchOffsetX * depth;
const y = DEFAULT_LAYOUT.tickY + depth * 120;  // 每个 branch 垂直偏移 120px
```

3. Tool 节点（tool.call 事件产生的节点）应在 parent tick 下方垂直排列

**涉及文件**:
- `apps/web/src/components/canvas/TickCanvas.tsx` — 位置计算逻辑
- `apps/web/src/types/canvas.ts` — 确认/调整 `CanvasLayout` 常量

**验收标准**:
1. main branch tick 在 Y=100
2. 第一个子 branch tick 在 Y=220（或类似偏移）
3. 不同 branch 的 tick 不重叠
4. 多 branch 场景下画布清晰可读

---

### C-4: tool.call 事件 data 中无 parent_tick_id — 边永远不创建

**根因分析**:
- 前端 `TickCanvas.tsx:38` 尝试从 `evt.data.parent_tick_id` 获取来源：
  ```typescript
  const parentId = (evt.data.parent_tick_id as string) || "";
  if (parentId) {
    es.push({ source: parentId, target: evt.tick_id, ... });
  }
  ```
- 但后端 `events.py:131-141` 中 `ToolCallEvent.create()` 的 `data` 字段只包含：
  ```python
  data = {
      "tool_name": tool_name,
      "arguments": arguments,
      "call_id": call_id,
  }
  ```
  **没有 `parent_tick_id` 字段**
- `parentId` 永远为空字符串 → `if (parentId)` 为 false → 边永远不创建
- Tick 节点之间无连线，canvas 看不到执行链

**修复方案**:

方案 A — 后端在 ToolCallEvent.data 中包含 tick_id：
- `ToolCallEvent` 本身已有 `tick_id` 字段（顶层），前端应使用 `evt.tick_id` 作为边的 target
- 前端的 `parentId` 应该是前一个 tick 的 ID，而非从 data 中获取

方案 B（推荐）— 正确的前端边构建逻辑：
- Tick-to-Tick 的顺序边：同一 branch 内，按 tick 的时间顺序连接
- Tick-to-Tool 的边：tool.call 事件的 `evt.tick_id` 是 parent，`call_id` 作为 tool 节点的 ID
- Tool-to-Tick 的边：tool.result 事件关联回

```typescript
// 1. Tick-to-Tick 顺序边（同一 branch）
// 2. Tick → ToolNode 边（使用 evt.tick_id 作为 source）
// 3. ToolNode → ToolResult 边
```

同时，后端也可在 `data` 中额外包含 `tick_id` 作为冗余（但前端主要逻辑需修正）。

**涉及文件**:
- `apps/web/src/components/canvas/TickCanvas.tsx` — 边构建逻辑
- `services/orchestrator/src/canvas/events.py` — 可选：在 ToolCallEvent.data 中加 tick_id

**验收标准**:
1. 同一 branch 内，Tick 节点按时间顺序用箭头连接
2. Tool 调用显示为从 Tick 节点分出的子节点
3. Tool 结果连回对应 Tool 节点
4. 多 tick + 多 tool 调用的场景下边关系正确

---

### C-5: wsClient.ts WebSocket 使用相对路径 — 连接必定失败

**根因分析**:
- `wsClient.ts:18-19`：
  ```typescript
  const url = `/ws/canvas?${params}`;
  this.ws = new WebSocket(url);
  ```
- `WebSocket()` 构造函数要求**绝对 URL**（`ws://host:port/path`）
- 传入 `/ws/canvas?...` 相对路径，浏览器抛出 `SyntaxError: Failed to construct 'WebSocket'`
- WebSocket 连接**永远无法建立**

**修复方案**:

```typescript
// 构建绝对 URL
const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const host = window.location.host;
const url = `${protocol}//${host}/ws/canvas?${params}`;
this.ws = new WebSocket(url);
```

**涉及文件**:
- `apps/web/src/lib/canvas/wsClient.ts` — connect 方法中 URL 构建

**验收标准**:
1. 本地开发环境（ws://localhost:port）能正常连接
2. 生产环境（wss://domain）能正常连接
3. 连接失败时触发 onerror 回调，不抛出构造异常

---

### C-6: event_store.py / events.py 序列化键不匹配

**根因分析**:
- 三方使用不同的键名表示事件类型：

| 组件 | 键名 |
|------|------|
| `events.py` `to_dict()` → 输出 | `"type": self.event_type` |
| `event_store.py` SQLite 列名 | `event_type` |
| `event_store.py` `_row_to_event_dict()` | 返回 `event_type`（从 SQLite 列名） |
| 前端 `canvas.ts` `CanvasEvent` | `type: CanvasEventType` |

**具体问题**:
1. `CanvasEvent.to_dict()` 输出 `"type"` 键 → 写入 SQLite 时通过 `event.event_type` 属性写入 `event_type` 列 ✓（属性名正确）
2. 从 SQLite 读取时 `_row_to_event_dict()` 返回 `"event_type"` 键（来自列名）
3. 前端期望 `"type"` 键（`CanvasEvent.type` 字段）
4. Replay 场景：从 SQLite 读出的 dict 键为 `event_type`，发送给前端后前端找不到 `type` 字段

**修复方案**:

在 `event_store.py` 的 `_row_to_event_dict()` 中重命名键：

```python
def _row_to_event_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["data"] = json.loads(d["data"])
    # 对齐前端期望的键名
    d["type"] = d.pop("event_type")
    return d
```

**涉及文件**:
- `services/orchestrator/src/canvas/event_store.py` — `_row_to_event_dict` 方法

**验收标准**:
1. `get_events()` 返回的 dict 包含 `type` 键（非 `event_type`）
2. 前端 replay 场景能正确解析事件类型
3. `CanvasEvent.to_dict()` 和 `_row_to_event_dict()` 输出格式一致

---

## WARNING 问题（14 个，按关键程度排序）

### W-1: tracker.py session_id fallback 到 branch_id（5 处错误）

**问题**: `tracker.py` 中 `record_token`, `record_tool_call`, `record_tool_result`, `complete_tick`, `fail_tick` 都有 `session_id or tick.branch_id` 的 fallback 逻辑。`branch_id` 和 `session_id` 是完全不同的概念，fallback 后事件会关联到错误的 session。

**修复方案**: 
- 所有方法签名中 `session_id` 和 `branch_id` 都改为必填参数（去掉默认空字符串）
- 去掉 `session_id or tick.branch_id` 的 fallback，改为 `session_id` 必须由调用方显式传入
- 调用方如果未传 session_id 则 raise ValueError

**涉及文件**: `tracker.py`

---

### W-2: TickCanvas.tsx useNodesState/useEdgesState 与 useMemo 冲突

**问题**: `TickCanvas.tsx:48-49` 使用 `useNodesState(nodes)` / `useEdgesState(edges)`，但 `nodes` 和 `edges` 由 `useMemo` 计算。`useNodesState` 内部会在 state 初始化后忽略新的 `nodes` prop（React Flow 的行为），导致状态更新时节点不刷新。

**修复方案**:
- 方案 A（推荐）— 去掉 `useNodesState/useEdgesState`，直接使用受控模式：
  ```tsx
  <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView />
  ```
- 方案 B — 使用 `useEffect` 同步 nodes/edges 到 state：
  ```tsx
  useEffect(() => { setNodes(nodes); setEdges(edges); }, [nodes, edges]);
  ```

**涉及文件**: `TickCanvas.tsx`

---

### W-3: BranchManager.tsx Branch 创建不调后端 API

**问题**: `BranchManager.tsx:146-158` 中 `handleCreate` 直接在 Zustand store 中创建本地 Branch 对象，不调用 `POST /api/canvas/branch/create`。后端不知道新 Branch 的存在。

**修复方案**:
```typescript
const handleCreate = async () => {
  const res = await fetch(`/api/canvas/branch/create?session_id=${sessionId}&parent_branch_id=${parentBranchId}&fork_tick_id=${forkTickId}`);
  const branchData = await res.json();
  // 将后端返回的 Branch 存入 store
  addBranch(branchData);
  addTab(branchData.branch_id, newName);
};
```
同时在 store 中添加 `parent_branch_id` 和 `fork_tick_id` 字段。

**涉及文件**: `BranchManager.tsx`, `canvasStore.ts`, `canvas.ts`（类型）

---

### W-4: BranchManager.tsx Merge 在前端伪造事件

**问题**: `handleMerge`（行 161-173）和 `handlePrune`（行 176-188）通过 `store.addEvent()` 在本地伪造 `branch.merged` / `branch.pruned` 事件，而非调用后端 API。这些事件只有前端知道，后端 EventStore 中不存在。

**修复方案**:
- Merge 调用 `POST /api/canvas/branch/merge`
- Prune 调用 `POST /api/canvas/branch/prune`
- 后端 API 负责发出真实事件并通过 WebSocket 广播
- 前端通过 WS 接收事件，而非本地伪造

**涉及文件**: `BranchManager.tsx`

---

### W-5: canvas.py REST 端点无输入校验

**问题**: 
- `create_branch`: `session_id` 必填但无格式验证；`fork_tick_id` 可为空字符串（空字符串不合法）
- `merge_branch`: 不验证 `target_branch_id` 是否存在或是否为合法目标
- `prune_branch`: `branch_id` 不验证是否在 `_branches` 字典中（返回 error 但无 HTTP 状态码区分）

**修复方案**:
- 使用 Pydantic model 定义请求参数，增加校验
- 合并/剪枝失败返回适当的 HTTP 状态码（404、409 等）
- `fork_tick_id` 为必填或验证非空

**涉及文件**: `canvas.py`

---

### W-6: kg_write_lock.py _async_locks dict 非线程安全

**问题**: `_get_async_lock()`（行 88-91）检查和创建 `asyncio.Lock` 时未使用 `_meta_lock` 保护。`_async_locks` dict 可能被多个线程/协程并发访问，存在竞态条件。

```python
def _get_async_lock(self, branch_id: str) -> asyncio.Lock:
    if branch_id not in self._async_locks:        # ← 无锁保护
        self._async_locks[branch_id] = asyncio.Lock()
    return self._async_locks[branch_id]
```

**修复方案**:
```python
def _get_async_lock(self, branch_id: str) -> asyncio.Lock:
    with self._meta_lock:
        if branch_id not in self._async_locks:
            self._async_locks[branch_id] = asyncio.Lock()
        return self._async_locks[branch_id]
```

**涉及文件**: `kg_write_lock.py`

---

### W-7: 前后端 Branch 接口完全不匹配

**问题**: 后端 `Branch` 和前端 `Branch` 类型字段差异大：

| 字段 | 后端 (branch.py) | 前端 (canvas.ts) |
|------|-------------------|-------------------|
| 名称 | 无 name 字段 | `name: string` |
| 父 tick | `fork_tick_id` | `parent_tick_id` |
| 状态 | `status: BranchStatus` | 无 status 字段 |
| 父分支 | `parent_branch_id` | 无此字段 |
| merged_at | 有 | 无 |

**修复方案**: 统一前后端 Branch 接口。以后端 `Branch.to_dict()` 为基准，前端类型对齐：

```typescript
export interface Branch {
  branch_id: string;
  session_id: string;
  parent_branch_id: string;
  fork_tick_id: string | null;
  name?: string;         // 可选，从 metadata 中取
  status: "active" | "merged" | "pruned" | "archived";
  created_at: string;
  merged_at: string | null;
}
```

**涉及文件**: `canvas.ts`（类型）, `BranchManager.tsx`, `canvasStore.ts`

---

### W-8: Layer2Panel handleSubmit 双重 JSON.stringify

**问题**: `Layer2Panel.tsx:26`:
```typescript
canvasWsClient.send(JSON.stringify(payload));
```
而 `wsClient.ts:63` 的 `send` 方法内部又做了一次 `JSON.stringify(data)`：
```typescript
send(data: unknown): void {
  this.ws.send(JSON.stringify(data));
}
```
结果是发送了**双重序列化**的字符串（JSON 字符串里面又包了一层 JSON 字符串）。

**修复方案**: `Layer2Panel` 调用 `canvasWsClient.send(payload)` 即可（去掉外层 `JSON.stringify`）。

**涉及文件**: `Layer2Panel.tsx`

---

### W-9: TickCanvas 中 tool.call 事件未生成对应的 Tool 节点

**问题**: `TickCanvas.tsx:36-43` 遍历 events 时，`tool.call` 事件只创建了边，没有创建 Tool 节点。边连接到一个不存在的 target 节点，React Flow 会忽略这些边。

**修复方案**: 在 `tool.call` 事件处理中同时创建 Tool 节点：
```typescript
if (evt.type === "tool.call" && evt.tick_id) {
  const toolNodeId = `tool_${evt.data.call_id}`;
  const parentTick = ticks.get(evt.tick_id);
  ns.push({
    id: toolNodeId,
    type: "toolNode",
    position: { x: parentTick?.x || 0, y: parentTickIdx * layout + 80 },
    data: { toolName: evt.data.tool_name, ... },
  });
  es.push({ source: evt.tick_id, target: toolNodeId });
}
```

**涉及文件**: `TickCanvas.tsx`

---

### W-10: CanvasEventStore SQLite 连接非线程安全

**问题**: `event_store.py:69` 使用 `check_same_thread=False` 允许多线程访问同一连接，但 SQLite 连接对象本身不是线程安全的。在多线程/async 场景下可能出现 `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`。

**修复方案**: 
- 添加 `threading.Lock` 保护写操作
- 或使用 `aiosqlite` 替代同步 sqlite3
- 或为每个线程/协程创建独立连接（连接池）

**涉及文件**: `event_store.py`

---

### W-11: TabBar 和 BranchManager 的 Tab 操作不走后端

**问题**: `TabBar.tsx` 和 `BranchManager.tsx` 中 Tab 的创建/删除/切换全部在 Zustand store 本地完成，不与后端 `TabManager` 同步。后端 TabManager 是死代码。

**修复方案**: 
- 短期：保持前端 Tab 本地管理（Tab 是纯 UI 概念，后端不必须感知）
- 中期：如果需要 Tab ↔ Branch 路由，通过 WS 命令同步（`cmd: "switch_branch"` 已有）

**涉及文件**: `TabBar.tsx`, `BranchManager.tsx`

---

### W-12: canvasStore addEvent 中 Tick 重建逻辑缺少 tool.call → tick 的关联

**问题**: `canvasStore.ts:100-112` 处理 `tool.call` 事件时，只检查 `nextTicks.get(e.tick_id)`，但如果 `tick.started` 事件还未到达（网络乱序），tool.call 会被丢弃。

**修复方案**: 与 `tick.completed` 处理类似，在 `tool.call` 处理中也添加 "tick 不存在则创建 stub" 的逻辑。

**涉及文件**: `canvasStore.ts`

---

### W-13: LODControl 缺少全局 LOD 切换逻辑

**问题**: `LODControl.tsx` 存在但未在审查范围内，需确认 LOD 切换是否正确更新所有节点的渲染层级。

**涉及文件**: `LODControl.tsx`

---

### W-14: 事件去重逻辑基于 event_id + type 组合

**问题**: `canvasStore.ts:10` 的去重键为 `e.event_id + ":" + e.type`，理论上 `event_id` 是 UUID，全局唯一，加 type 只是冗余。但如果两个不同类型的事件偶然共享 event_id（不应发生但防御性编程），会错误去重。

**修复方案**: 改为仅以 `event_id` 去重：
```typescript
const k = e.event_id; if (seen.has(k)) return false;
```

**涉及文件**: `canvasStore.ts`

---

## 架构层面问题：前后端完全脱节

### 问题诊断

当前系统的核心问题是**前端是自闭环的**：

1. **前端所有操作在 Zustand store 本地完成**：
   - Tab 创建/删除 → `canvasStore.addTab/removeTab`
   - Branch 创建 → `BranchManager.handleCreate` 直接构造本地对象
   - Branch 合并/剪枝 → `store.addEvent()` 伪造本地事件
   - Layer2 Submit → `canvasWsClient.send()` 发送但后端无 handler

2. **后端有大量死代码**：
   - `TabManager` — 从未被前端使用
   - REST 端点 `POST /api/canvas/branch/*` — 从未被前端调用
   - `ToolResultNode`、`PromptNode`、`AgentNode` 等组件 — 存在但未集成到 TickCanvas

3. **WebSocket 连接未建立**（C-5 导致），所以前端完全不知道后端事件的存在

### 架构修复策略：三阶段对齐

#### Phase 1: 管道打通（依赖 C-1, C-5, C-6）

1. 修复 C-1（tracker async）— 后端事件开始流动
2. 修复 C-5（WS URL）— 前端能连接 WebSocket
3. 修复 C-6（序列化键）— 前端能正确解析事件

验收：后端 `tracker.start_tick()` 后，前端 TickCanvas 实时显示 Tick 节点

#### Phase 2: 数据流对齐（依赖 W-3, W-4, W-7）

1. 前端 Branch 操作改为调后端 API（W-3, W-4）
2. 前后端 Branch 类型对齐（W-7）
3. 前端不再伪造事件，所有事件来自后端

验收：创建/合并/剪枝 Branch 后，后端 EventStore 有记录，前端通过 WS 收到广播

#### Phase 3: 完整 Canvas 交互（依赖 C-3, C-4, W-9）

1. 修复节点位置（C-3）— Branch 分叉可视觉区分
2. 修复边构建（C-4）— Tick 链和 Tool 调用有连线
3. 添加 Tool 节点（W-9）— Tool 调用在 Canvas 上可见

验收：完整的 Tick→Tool→Result 链路在 Canvas 上可视化

---

## 修复顺序和依赖关系

```
阶段 1: 管道打通（CRITICAL，阻塞性）
├── C-1: tracker async 化            ← 最高优先级，后端基础
├── C-5: WS URL 绝对路径              ← 前端基础
├── C-6: 序列化键对齐                 ← 数据基础
└── W-6: kg_write_lock 线程安全       ← 可并行

阶段 2: 安全 + 核心逻辑
├── C-2: WS 身份验证                  ← 依赖 C-5（先能连上再加认证）
├── C-3: 节点位置按 branch 区分        ← 依赖阶段1（需能看到节点）
├── C-4: tool.call 边构建             ← 依赖 C-1（需事件流动）
├── W-1: tracker session_id fallback  ← 依赖 C-1（一起改）
├── W-2: useNodesState 冲突           ← 可并行
└── W-5: REST 输入校验                ← 可并行

阶段 3: 前后端对齐
├── W-3: Branch 创建调后端 API        ← 依赖阶段1+2
├── W-4: Merge/Prune 调后端 API       ← 依赖 W-3
├── W-7: Branch 类型对齐              ← 依赖 W-3
├── W-8: Layer2 双重 stringify        ← 可并行
├── W-9: Tool 节点生成                ← 依赖 C-4
└── W-10: EventStore 线程安全         ← 可并行

阶段 4: 收尾
├── W-11: Tab 后端同步（低优先级）
├── W-12: tool.call stub 逻辑
├── W-13: LOD 确认
└── W-14: 去重键简化
```

---

## 预估工作量

| 阶段 | 内容 | 预估时间 |
|------|------|----------|
| 阶段 1 | C-1 + C-5 + C-6 + W-6 | 2-3h |
| 阶段 2 | C-2 + C-3 + C-4 + W-1 + W-2 + W-5 | 4-5h |
| 阶段 3 | W-3 + W-4 + W-7 + W-8 + W-9 + W-10 | 4-6h |
| 阶段 4 | W-11 + W-12 + W-13 + W-14 | 2-3h |
| 测试验证 | 全流程集成测试 | 2-3h |
| **合计** | | **14-20h** |

---

## 测试策略

### 单元测试
- `test_tracker.py` — 验证 async emit 被正确 await
- `test_event_store.py` — 验证 `_row_to_event_dict` 输出包含 `type` 键
- `test_kg_write_lock.py` — 验证 `_get_async_lock` 线程安全

### 集成测试
- 启动后端 → WS 客户端连接 → tracker 发出事件 → 客户端收到事件
- Branch 创建 API → WS 广播 branch.created → 前端 store 更新

### E2E 测试
- 完整 Tick 生命周期：start → token → tool.call → tool.result → completed
- Branch 分叉 → 多 Tab → Branch 合并
- 断线重连 → replay 补齐

---

*修复计划 v1，待执行*
