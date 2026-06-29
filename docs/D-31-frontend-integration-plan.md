# D-31 前端集成计划

> ⚠️ **历史文档**(写于当时, 记录当时的实现计划)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

> 编写时间: 2026-04-24
> 状态: Draft（待评审）

---

## 1. 现状分析

### 1.1 旧 FlowCanvas 架构（编辑器模式）

**定位**: 静态可视化编排器 — 用户拖拽创建 Agent/Tool/Prompt 节点，手动连线，保存到 localStorage。

**组件树**:
```
FlowCanvas
├── ReactFlow (@xyflow/react)
│   ├── AgentNode (拖拽放置，双击执行)
│   ├── ToolNode
│   └── PromptNode
├── Background / Controls / MiniMap
└── DataEdge
```

**数据源**: `flowStore.ts` — Zustand store
- `nodes: Node[]` / `edges: Edge[]` — 直接存储 React Flow 节点
- localStorage 持久化（500ms debounce）
- 拖放创建节点 → `createAgent()` API 调用
- SSE 执行 (`executeWithSSE`)

**引用位置**:
| 页面 | 文件 | 用途 |
|------|------|------|
| `/canvas` | `app/canvas/page.tsx` | FlowCanvas + Sidebar + Header + PropertyPanel + ExecutePanel |
| `/flows` | `app/flows/page.tsx` | FlowCanvas + Sidebar + Agent 拖拽列表 + PropertyPanel + ExecutePanel |

**配套组件**:
- `PropertyPanel` — 节点属性编辑（Agent 配置、模型选择、System Prompt、Memory 查看）
- `ExecutePanel` — Agent SSE 执行面板
- `nodes/AgentNode.tsx`, `nodes/ToolNode.tsx`, `nodes/PromptNode.tsx`
- `edges/DataEdge.tsx`

**类型**: `types/flow.ts` — `FlowNodeType = "agent" | "tool" | "prompt"`, `FlowEdgeType = "data"`

### 1.2 D-31 TickCanvas 架构（实时观测模式）

**定位**: 事件驱动的实时 Canvas — 通过 WebSocket 接收 Tick/Branch/Scoring 事件，自动渲染 Tick 时间线 + 工具分叉 + 分支偏移。

**组件树**:
```
TickCanvas
├── TabBar (多 Tab 切换)
├── LODControl (L1/L2/L3 细节级别)
├── ScoringOverlay (SVG 热力图，L2+ 显示)
├── ReactFlow (@xyflow/react)
│   ├── TickNode (L1/L2/L3 三级渲染)
│   ├── CommitteeVoteNode (Committee 投票)
│   ├── ToolNode (工具调用)
│   └── ToolResultNode (工具结果，L1/L2/L3)
└── (边: smoothstep — 顺序 + 工具分叉)
```

**未在 TickCanvas 内部但属于 D-31 生态的组件**:
| 组件 | 文件 | 职责 |
|------|------|------|
| `BranchManager` | `components/canvas/BranchManager.tsx` | Branch CRUD、Merge、Prune、跨 Tab 发送 |
| `Layer2Panel` | `components/canvas/Layer2Panel.tsx` | Layer 2 Staging 暂存区（Text + Command 节点组装） |
| `ScoringOverlay` | `components/canvas/ScoringOverlay.tsx` | SVG scoring.signal 热力图叠加层 |

**数据源**: `canvasStore.ts` — Zustand store
- `ticks: Map<string, Tick>` — 从 WS 事件实时构建
- `events: CanvasEvent[]` — 事件流（最大 2000 条，去重）
- `branches: Branch[]` — 分支管理
- `tabs: CanvasTab[]` — 多 Tab 管理
- `lod: LODLevel` — 全局细节级别
- `connected: boolean` — WS 连接状态

**WebSocket**: `wsClient.ts` — `CanvasWSClient` 单例
- `connect(sessionId, afterEventId?, token?)` — 带指数退避重连
- 自动回放最后事件 ID 实现断点续传
- `send(data)` — Layer 2 提交等上行消息
- 不可重连 close code: 4001, 4003

**类型**: `types/canvas.ts` — 完整的事件溯源类型系统
- `CanvasEvent` — 事件联合类型 (tick.started/completed, tool.call/result, branch.*, scoring.signal, committee.vote)
- `Tick` — 请求/响应/摘要/工具调用 + LOD 支持
- `Branch`, `CanvasTab`, `Layer2Node`, `ToolCallInfo`
- `DEFAULT_LAYOUT` — Canvas 布局常量

**配套 Store**: `layer2Store.ts` — Layer 2 暂存区状态管理

### 1.3 关键差异对比

| 维度 | FlowCanvas（旧） | TickCanvas（D-31） |
|------|------------------|---------------------|
| **数据源** | localStorage 静态数据 | WebSocket 实时事件流 |
| **交互模式** | 拖拽创建 + 手动连线 | 只读观测 + LOD 切换 |
| **节点类型** | Agent/Tool/Prompt (3) | Tick/Tool/ToolResult/CommitteeVote (4) |
| **Store** | `flowStore` (nodes/edges) | `canvasStore` (ticks/events/branches/tabs) |
| **类型定义** | `types/flow.ts` (4 行) | `types/canvas.ts` (140+ 行) |
| **Store 互引** | 引用 `agentStore` | 引用无外部 store |
| **后端依赖** | `/api/agents/*`, `/api/execute` SSE | `/ws/canvas`, `/api/canvas/branch/*` |
| **连接状态** | 无（REST + SSE 单次） | WS 长连接 + 重连 |
| **被路由引用** | ✅ `/canvas` + `/flows` | ❌ 无任何路由引用（孤岛） |

### 1.4 孤岛范围

以下 D-31 文件**未被任何页面路由直接引用**：

```
components/canvas/TickCanvas.tsx          ← 核心 Canvas，无路由使用
components/canvas/TabBar.tsx              ← TickCanvas 内部引用
components/canvas/LODControl.tsx          ← TickCanvas 内部引用
components/canvas/ScoringOverlay.tsx      ← 未被 TickCanvas 引用！
components/canvas/BranchManager.tsx       ← 未被 TickCanvas 引用！
components/canvas/Layer2Panel.tsx         ← 未被 TickCanvas 引用！
components/canvas/nodes/TickNode.tsx      ← TickCanvas 内部引用
components/canvas/nodes/CommitteeVoteNode.tsx ← TickCanvas nodeTypes 注册但无事件源
components/canvas/nodes/ToolResultNode.tsx ← TickCanvas 内部引用
stores/canvasStore.ts                     ← TickCanvas 引用
stores/layer2Store.ts                     ← Layer2Panel 引用
lib/canvas/wsClient.ts                    ← BranchManager + Layer2Panel 引用
types/canvas.ts                           ← 所有 D-31 组件引用
```

**关键发现**: `ScoringOverlay`、`BranchManager`、`Layer2Panel` 三个 D-31 组件甚至没有被 `TickCanvas` 内部引用 — 它们需要被**新的页面级组件**编排组合。

---

## 2. 方案对比

### 方案 A：替换旧 FlowCanvas → TickCanvas（完全替换）

**做法**: 将 `/canvas` 路由从 FlowCanvas 切换到 TickCanvas，废弃旧的画布页面。

**优势**:
- 简单直接，一个页面改动
- 避免两套 Canvas 共存的维护负担

**劣势**:
- ❌ `/flows` 页面仍依赖 FlowCanvas（Agent 拖拽 + 执行），不能删
- ❌ PropertyPanel 和 ExecutePanel 绑定 `flowStore`，D-31 Canvas 不需要它们
- ❌ FlowCanvas 提供的"可视化编排"功能（拖拽建 Agent、连线、配置）丢失
- ❌ 两种 Canvas 解决完全不同的问题（编排 vs 观测），不应互相替代

**工作量**: 小（改 1 个文件）
**风险**: 高（功能回退，用户丢失编排能力）
**结论**: ❌ **不可行**

### 方案 B：新建独立路由 `/canvas/live`（共存）

**做法**: 新增 `/canvas/live` 路由，作为 D-31 TickCanvas 的专属页面。`/canvas` 和 `/flows` 保持不变。

**页面结构**:
```
/canvas     → FlowCanvas（编辑器模式，不变）
/flows      → FlowCanvas（编辑器模式 + Agent 拖拽，不变）
/canvas/live → TickCanvas（实时观测模式，新增）
```

**优势**:
- ✅ 零风险 — 不影响现有页面
- ✅ 两种 Canvas 各司其职，清晰分离
- ✅ 可独立迭代、独立测试
- ✅ 导航栏/侧边栏可自然扩展入口
- ✅ 向后兼容 — FlowCanvas 在 `/canvas` 和 `/flows` 仍可用

**劣势**:
- 需要新建 1 个路由文件 + 可能需要布局组件
- 用户需要知道去哪个页面（通过导航引导解决）
- ScoringOverlay / BranchManager / Layer2Panel 需要在新页面手动编排

**工作量**: 中（新建 1 路由 + 1 页面组件，约 200 行代码）
**风险**: 低
**结论**: ✅ **推荐**

### 方案 C：TickCanvas 作为 FlowCanvas 的子组件嵌入

**做法**: 在 FlowCanvas 内部条件渲染 TickCanvas（如通过 Toggle 切换模式）。

**优势**:
- 同一 URL 下两种模式可切换

**劣势**:
- ❌ 两种 Canvas 的数据源完全不同（flowStore vs canvasStore + WS），混在一个页面会混乱
- ❌ PropertyPanel / ExecutePanel 绑定 flowStore，TickCanvas 不使用它们 — 需要条件隐藏
- ❌ WS 连接生命周期管理困难（什么时候连？什么时候断？）
- ❌ 违反单一职责 — 一个页面做两件不相关的事
- ❌ 复杂度高，容易引入 bug

**工作量**: 大（需要大量条件逻辑和重构）
**风险**: 高
**结论**: ❌ **不推荐**

---

## 3. 推荐方案：方案 B（独立路由 `/canvas/live`）

### 3.1 理由

1. **零风险** — 现有功能完全不受影响
2. **职责清晰** — `/canvas` + `/flows` = 编辑器模式（FlowCanvas），`/canvas/live` = 实时观测模式（TickCanvas）
3. **增量交付** — 可以先最小可用（仅 TickCanvas + WS 连接），后续逐步添加 BranchManager / Layer2Panel / ScoringOverlay
4. **代码整洁** — 新页面组件只编排 D-31 组件，不引入 flowStore 依赖

### 3.2 未来演进路径

```
Phase 1 (本计划): /canvas/live 基础页面 — TickCanvas + WS 连接
Phase 2:         + BranchManager 侧边栏
Phase 3:         + Layer2Panel 右侧栏
Phase 4:         + ScoringOverlay 叠加层
Phase 5:         Session 选择器（连接到不同 Agent Session）
Phase 6:         (远期) /canvas 重构为入口页，引导到 /canvas/edit 和 /canvas/live
```

---

## 4. 实施步骤

### Step 1：新建页面路由

**文件**: `apps/web/src/app/canvas/live/page.tsx`（新建）

**内容**:
```tsx
"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/Header";
import { TickCanvas } from "@/components/canvas/TickCanvas";
import { BranchManager } from "@/components/canvas/BranchManager";
import { Layer2Panel } from "@/components/canvas/Layer2Panel";
import { ScoringOverlay } from "@/components/canvas/ScoringOverlay";
import { useCanvasStore } from "@/stores/canvasStore";
import { canvasWsClient } from "@/lib/canvas/wsClient";
import { Wifi, WifiOff } from "lucide-react";

export default function CanvasLivePage() {
  const connected = useCanvasStore(s => s.connected);
  const sessionId = useCanvasStore(s => s.sessionId);
  const [inputSessionId, setInputSessionId] = useState("");

  // WS 连接管理
  useEffect(() => {
    // 如果 URL 有 session_id 参数，自动连接
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("session_id") || sessionId;
    if (sid) {
      canvasWsClient.connect(sid);
    }
    return () => canvasWsClient.disconnect();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function handleConnect() {
    if (!inputSessionId.trim()) return;
    canvasWsClient.connect(inputSessionId.trim());
  }

  return (
    <div className="flex h-screen w-screen flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        {/* Left: BranchManager */}
        <BranchManager />
        {/* Center: TickCanvas + ScoringOverlay */}
        <div className="flex flex-1 flex-col relative">
          {/* Connection bar */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-50 border-b border-gray-200">
            {connected ? (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <Wifi className="h-3.5 w-3.5" /> Connected
              </span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-gray-400">
                <WifiOff className="h-3.5 w-3.5" /> Disconnected
              </span>
            )}
            {!connected && (
              <form onSubmit={(e) => { e.preventDefault(); handleConnect(); }} className="flex gap-1 ml-2">
                <input
                  value={inputSessionId}
                  onChange={e => setInputSessionId(e.target.value)}
                  placeholder="Session ID..."
                  className="px-2 py-0.5 text-xs border border-gray-200 rounded focus:outline-none focus:border-blue-400"
                />
                <button type="submit" disabled={!inputSessionId.trim()}
                  className="px-2 py-0.5 text-xs bg-blue-600 text-white rounded disabled:opacity-40">
                  Connect
                </button>
              </form>
            )}
          </div>
          {/* Canvas */}
          <div className="flex-1 relative">
            <TickCanvas />
            <ScoringOverlay />
          </div>
        </div>
        {/* Right: Layer2Panel */}
        <Layer2Panel />
      </div>
    </div>
  );
}
```

**改动**: 新建 1 个文件，约 70 行

### Step 2：ScoringOverlay 集成到 TickCanvas 容器

**说明**: ScoringOverlay 当前是绝对定位的 SVG 叠加层，需要放在与 TickCanvas 同级的 `relative` 容器内。

**改动**: 在 Step 1 的页面中已处理 — `<div className="flex-1 relative">` 包裹 TickCanvas + ScoringOverlay。

**注意**: ScoringOverlay 的 `viewBox` 计算基于自身数据而非 React Flow viewport，可能需要对齐。后续可考虑将其作为 React Flow 的 Panel 组件嵌入。

### Step 3：导航入口

**文件**: `apps/web/src/components/layout/Sidebar.tsx`（修改）

**改动**: 在 Sidebar 中添加 "Live Canvas" 导航链接，指向 `/canvas/live`。

```tsx
<a href="/canvas/live" className="...">📡 Live Canvas</a>
```

**改动量**: 约 5 行

### Step 4：WS 认证适配

**文件**: `apps/web/src/lib/canvas/wsClient.ts`（可能修改）

**说明**: `CanvasWSClient.connect()` 接受可选 `token` 参数。需要确认：
1. JWT token 如何获取（登录流程？localStorage？）
2. 是否需要自动附带当前用户的认证 token

**改动**: 如果需要自动认证，修改 `connect()` 从 localStorage 或 cookie 读取 token。约 5 行。

### Step 5（可选）：URL 参数自动连接

**文件**: `apps/web/src/app/canvas/live/page.tsx`（Step 1 中已部分处理）

**说明**: 支持 `?session_id=xxx` URL 参数自动连接 WS。在 Step 1 的 `useEffect` 中已包含基础逻辑。

**增强**: 从其他页面（如 Agent 详情页）点击 "Watch Live" 链接跳转到 `/canvas/live?session_id=xxx`。

### Step 6（后续）：CommitteeVoteNode 事件接入

**说明**: `CommitteeVoteNode` 已在 TickCanvas 的 `nodeTypes` 中注册，但当前 `TickCanvas` 的事件→节点计算逻辑（`useMemo` 块）**未处理 `committee.vote` 事件**。需要扩展 useMemo 以创建 CommitteeVote 节点。

**文件**: `apps/web/src/components/canvas/TickCanvas.tsx`

**改动**: 在 useMemo 中添加 committee.vote 事件处理，约 30 行。

---

## 5. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `apps/web/src/app/canvas/live/page.tsx` | **新建** | D-31 Live Canvas 页面 |
| `apps/web/src/components/layout/Sidebar.tsx` | 修改 | 添加导航链接 |
| `apps/web/src/lib/canvas/wsClient.ts` | 可能修改 | 自动认证适配 |
| `apps/web/src/components/canvas/TickCanvas.tsx` | 后续修改 | CommitteeVote 事件接入 |

**不需要修改的文件**:
- `app/canvas/page.tsx` — 保持 FlowCanvas 不变
- `app/flows/page.tsx` — 保持不变
- `components/canvas/FlowCanvas.tsx` — 保持不变
- `stores/flowStore.ts` — 保持不变
- `types/flow.ts` — 保持不变
- 所有 D-31 组件 — 保持不变（仅被新页面引用）

---

## 6. 风险点

### 6.1 WS 后端未就绪

**风险**: `/ws/canvas` 端点可能尚未部署或返回 404。
**影响**: Live Canvas 页面无法连接，但页面本身可正常渲染。
**应对**: 添加连接状态提示 + 错误处理。后端 E2E 验证已确认 WS 端点存在（2026-04-23 记录）。

### 6.2 Session ID 获取

**风险**: 用户不知道应该填什么 Session ID。
**影响**: 功能可用但体验差。
**应对**:
- 短期: 支持 URL 参数 `?session_id=xxx`
- 中期: 添加 Session 选择器（从 `/api/sessions` 获取列表）
- 长期: Agent 详情页提供 "Watch Live" 按钮

### 6.3 ScoringOverlay 位置偏移

**风险**: ScoringOverlay 的 SVG viewBox 是自包含的，可能与 React Flow canvas 的 viewport/zoom 不对齐。
**影响**: L2/L3 模式下热力图位置错误。
**应对**: 短期作为独立叠加层渲染（可接受）；后续考虑转为 React Flow Panel 或自定义 Node。

### 6.4 CommitteeVoteNode 未接入

**风险**: `CommitteeVoteNode` 已注册但无事件→节点映射逻辑，不会被渲染。
**影响**: Committee 投票事件不可视化（功能缺失但非阻塞）。
**应对**: Step 6 单独处理，不阻塞 Phase 1 交付。

### 6.5 BranchManager API 调用

**风险**: BranchManager 调用 `/api/canvas/branch/create|merge|prune`，这些 API 端点可能尚未部署。
**影响**: 创建/合并/修剪分支功能不可用。
**应对**: BranchManager 已有 `console.error` 错误处理，不会导致页面崩溃。可在 UI 上添加 "API 未就绪" 提示。

### 6.6 Layer2Panel 依赖 WS 连接

**风险**: Layer2Panel 的 submit 通过 `canvasWsClient.send()` 发送，需要 WS 已连接。
**影响**: WS 未连接时提交无效（`readyState !== OPEN`，send 静默忽略）。
**应对**: 在 Layer2Panel 中检查连接状态，未连接时禁用 Submit 按钮并提示。

---

## 7. 验收标准

### Phase 1 最小可用

- [ ] `/canvas/live` 页面可正常加载
- [ ] 页面布局：左侧 BranchManager、中间 TickCanvas、右侧 Layer2Panel
- [ ] 输入 Session ID 可连接 WS（绿灯亮起）
- [ ] WS 事件可驱动 Tick 节点实时渲染
- [ ] LOD 切换 (L1/L2/L3) 正常工作
- [ ] TabBar 多 Tab 切换正常
- [ ] `/canvas` 和 `/flows` 页面完全不受影响
- [ ] Sidebar 导航包含 Live Canvas 入口

### Phase 2 扩展

- [ ] ScoringOverlay 在 L2+ 模式下正确渲染
- [ ] CommitteeVoteNode 从 committee.vote 事件渲染
- [ ] BranchManager CRUD 操作正常
- [ ] Layer2Panel 提交功能正常
- [ ] URL 参数 `?session_id=xxx` 自动连接
