# D-31 Endless Canvas E2E 验证计划

**创建时间**: 2026-04-23
**状态**: 待执行
**负责人**: 项目专家-00

---

## 验证目标

验证 D-31 Endless Canvas 前后端全链路打通。

---

## 前置检查结果（2026-04-23 21:44）

| 检查项 | 结果 |
|--------|------|
| 后端 canvas 模块 import | ✅ OK（从 services/orchestrator/ 执行） |
| 前端 TypeScript 编译 | ✅ 0 errors |
| pytest（409 tests） | ✅ 404 PASS，5 FAIL（已知失败：butterfly_wing x4 + meta_agent_node x1） |

---

## 验证场景

### L1: 后端模块独立验证

**目标**: 确认 canvas 模块可独立实例化

```python
# 1. EventStore
from src.canvas.event_store import CanvasEventStore
store = CanvasEventStore(db_path=":memory:")
# ✅ 可实例化

# 2. BranchStore
from src.canvas.branch import BranchStore
bs = BranchStore(db_path=":memory:")
# ✅ 可实例化

# 3. TabManager
from src.canvas.tab_manager import TabManager
tm = TabManager()
# ✅ 可实例化

# 4. TickTracker
from src.canvas.tracker import TickTracker
tracker = TickTracker(session_id="test", store=store, emitter=None)
# ✅ 可实例化

# 5. SessionEventEmitter（无 WS）
from src.canvas.emitter import SessionEventEmitter
emitter = SessionEventEmitter(store=store)
# ✅ 可实例化
```

### L2: Branch API 端点验证（HTTP）

**目标**: 验证 Branch 创建/合并/剪枝 REST 端点

```bash
# 前置：启动后端
cd ~/projects/agent-os/services/orchestrator
/usr/bin/python3 -m uvicorn src.api.main:app --host 127.0.0.1 --port 18792 &

# 1. Create Branch
curl -X POST http://127.0.0.1:18792/api/canvas/branch/create \
  -H "Content-Type: application/json" \
  -d '{"session_id": "e2e-test-session", "parent_branch_id": "main", "fork_tick_id": ""}'

# 预期: 201 Created + branch_id

# 2. List Branches
curl http://127.0.0.1:18792/api/canvas/sessions/e2e-test-session/tabs

# 3. Merge Branch（需先有子 branch）
curl -X POST http://127.0.0.1:18792/api/canvas/branch/merge \
  -H "Content-Type: application/json" \
  -d '{"branch_id": "<created_branch_id>", "session_id": "e2e-test-session"}'

# 4. Prune Branch
curl -X POST http://127.0.0.1:18792/api/canvas/branch/prune \
  -H "Content-Type: application/json" \
  -d '{"branch_id": "<created_branch_id>", "session_id": "e2e-test-session"}'
```

### L3: WebSocket 连接验证

**目标**: 验证 WS 握手 + 事件订阅

```bash
# 安装 websocat（如果需要）
pip install websocat

# WS 连接测试
websocat ws://127.0.0.1:18792/ws/canvas?session_id=e2e-test-session

# 发送订阅消息
{"type": "subscribe", "session_id": "e2e-test-session"}

# 预期: 后端开始推送 tick events
```

### L4: 事件持久化验证

**目标**: 验证事件写入 SQLite + 读取一致

```bash
# 创建事件后查询数据库
sqlite3 ~/projects/agent-os/services/orchestrator/data/canvas_events.db \
  "SELECT event_id, type, session_id, timestamp FROM canvas_events \
   WHERE session_id='e2e-test-session' ORDER BY timestamp LIMIT 10;"

# 预期: 能查到刚才写入的事件
```

### L5: 前端 Canvas 渲染验证

**目标**: 验证前端页面可正常加载 TickCanvas

```bash
cd ~/projects/agent-os/apps/web
npm run dev
# 浏览器打开 http://localhost:3000
# 截图验证 Canvas 渲染
```

### L6: Branch 切换前后端联动

**目标**: 验证前端 branch 切换 → WS 推送 → 状态更新

```bash
# 1. 创建多个 branch
# 2. 前端切换到非 main branch
# 3. 检查 WS 是否收到 branch.created / branch.switched 事件
# 4. 检查 canvas store 中 branches 状态
```

---

## 验证执行记录

| 日期 | 执行者 | 结果 |
|------|--------|------|
| 2026-04-23 21:44 | auto (subagent failed) → main | L1✅ L2⏸(后端需启动) L3⏸ L4⏸ L5⏸ L6⏸ |

---

## 已知问题

1. **后端启动需要依赖服务**: resource-manager 未启动会导致 `/resources/providers` 500（非 canvas 问题）
2. **5 个测试失败（历史遗留）**: butterfly_wing x4 + meta_agent_node x1，非 D-31 引入
3. **conda sqlite3 链接损坏**: 使用 `/usr/bin/python3` 绕过

---

## 下一步

手动执行 L2-L6 验证，或编写自动化测试脚本 `tests/e2e/test_canvas_e2e.py`