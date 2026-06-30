# Agentic Test · intent.md 规划

> 为 Phase 0 `test-run <url> <intent.md>`(Tailscale+Playwright+Stagehand CLI)准备**意图输入**。
> **反向逻辑**:基于当前 agent-os-v2 前端路由 + 后端 API 实际实现,推导用户能做什么,编写自然语言意图(**intent 非脚本** —— Stagehand 读意图自主执行,不写 Playwright 选择器)。
> 生成:2026-06-28,基于 HEAD `73c3d5a`(前端 /v1+WS rewrite 修复后)。

## intent.md 格式

```markdown
---
id: <kebab-case>
title: <用户操作一句话>
page: <前端路由,如 /agents>
api: <后端端点,如 POST /v1/agents>
priority: P0|P1|P2
---

# 意图
<自然语言:用户想达成什么,为什么>

# 前置
<执行前需满足的状态,如"已登录""存在一个 agent">

# 步骤(自然语言,agentic 自主执行,非脚本)
1. <用户视角的操作,如"在名称框输入X">
2. ...

# 验证(成功判据,可观测)
- <UI 可见变化,如"agent 出现在列表">
- <API 可验证,如"GET /api/agents 含该 agent">

# 失败模式(可选,已知边界)
- <易错点,如"model 必填">
```

**原则**:intent 描述**用户意图**(做什么+为什么),**不**写选择器/代码(agentic 读意图自主导航)。验证用**可观测判据**(UI + API),非 DOM 断言。

## 目录结构

```
docs/agentic-test/
├── intent-plan.md          # 本文档(格式+清单+优先级)
└── intents/                # intent.md 集合(按用户操作)
    ├── P0/                 # 核心闭环(北极星,先跑通)
    ├── P1/                 # agent 生命周期 + 工具 + canvas
    └── P2/                 # 通信/debug/边界/鉴权
```

## intent 清单(反向推导自代码实现)

### P0 · 核心闭环(MVP 北极星,agentic test 必跑)

| id | 用户操作 | page | api | 文件 |
|---|---|---|---|---|
| `create-agent` | 创建一个 agent(名称+model+system_prompt) | /agents | POST /v1/agents | intents/P0/create-agent.md |
| `single-agent-chat` | 与 agent 单轮对话(走 /execute SSE) | / | POST /v1/execute | intents/P0/single-agent-chat.md |
| `multi-agent-orchestrate` | 多 agent 编排(researcher+writer→综合) | /memory(编排 tab) | POST /v1/orchestrate | intents/P0/multi-agent-orchestrate.md |
| `memory-recall` | 对话后召回记忆(/memories) | /memory(记忆 tab) | GET /v1/memories | intents/P0/memory-recall.md |

### P1 · agent 生命周期 + 工具 + canvas

| id | 用户操作 | page | api |
|---|---|---|---|
| `delete-agent` | 删除一个 agent | /agents | DELETE /v1/agents/:id |
| `agent-persist-restart` | 重启 orchestrator 后 agent 仍在 | /agents | GET /v1/agents(重启后) |
| `fc-tool-call` | 对话中 LLM 调工具(file_read 等) | / | POST /v1/execute(tool_use) |
| `multi-turn-fc` | 多轮工具调用循环(tool→llm→synthesize) | / | POST /v1/execute |
| `canvas-live-ws` | canvas 实时事件流(WS 连接) | /canvas/live | WS /ws/canvas |
| `canvas-layer2-submit` | canvas Layer2 提交(cmd 协议) | /canvas/live | WS layer2.submit |

### P2 · 通信/debug/边界/鉴权

| id | 用户操作 | page | api |
|---|---|---|---|
| `login` | JWT 登录 | /login | POST /auth(login) |
| `agent-communication` | agent 间通信(agent_message SSE) | /memory(通信 tab) | SSE agent_message |
| `execution-history` | 执行历史(node_* 事件) | /memory(history tab) | SSE node_* |
| `pitfall-query` | PitFail 踩坑查询 | —(API) | GET /v1/pitfall |
| `agent-list-empty` | 边界:无 agent 时列表空 | /agents | GET /v1/agents(空) |
| `orchestrate-no-orchestrator` | 边界:编排缺 orchestrator 报错 | /memory | POST /v1/orchestrate(404) |

## 优先级依据(反向自代码)

- **P0** = MVP boundary_in(Phase 0-3 北极星:创建/对话/编排/记忆召回),agentic test 必须跑通证明闭环
- **P1** = agent 生命周期(持久化/删除)+ FC 工具(defer 通电)+ canvas 实时(L3 通电),核心但非 MVP 闸门
- **P2** = 通信/debug/边界/鉴权,验证完整性 + 错误处理

## 编写状态

- ✅ 已写 P0(4 个:create-agent/single-agent-chat/multi-agent-orchestrate/memory-recall)
- ✅ 已写 P1(6 个:delete-agent/agent-persist-restart/fc-tool-call/multi-turn-fc/canvas-live-ws/canvas-layer2-submit)
- ✅ 已写 P2(6 个:login/agent-communication/execution-history/pitfall-query/agent-list-empty/orchestrate-no-orchestrator)
- **全部 16 个 intent 完成,已转 qa-agent-farm v0.2 标准格式**(fan-out workflow:frontmatter `name`/`target`(完整 URL)/`tags`/`timeout_ms` + 步骤 `act`/`observe`/`extract` 前缀 + 权威信号;对齐 Stagehand 运行时,否则 agentic test 跑不动)。v0.2 模板见 `qa-agent-farm/intents/_TEMPLATE.md`。
- agentic test Phase 0/1(CLI `test-run` / MCP skill)待实施

## 审核结果(2026-06-30,HEAD deec67c vs intent 写于 73c3d5a 06-28)

16 intent 对齐当前实现审核(fan-out 3 Agent 读 intent + 对比 apps/web/src + services/*/src):

| 符合性 | intent |
|---|---|
| ✅ 符合(7,未改) | agent-persist-restart / fc-tool-call / multi-turn-fc / canvas-layer2-submit / agent-communication / agent-list-empty / orchestrate-no-orchestrator |
| ⚠️ 过时已改(6) | single-agent-chat(无选择器,自动选 agents[0])/ memory-recall(客户端 content 过滤,非后端 ?query= recall)/ delete-agent(native confirm 非 modal)/ canvas-live-ws(B1 对重连无效→全量重放)/ login(default_skip,AUTH_ENABLED 默认 false)/ pitfall-query(末尾斜杠 404,target :8001) |
| ❌ NOT-WIRED(3,已标注 status) | create-agent(/agents UI 无创建表单,只一键按钮)/ multi-agent-orchestrate(gateway 缺 /orchestrate 代理)/ execution-history(无 history tab/Run 分组 UI,已重写对齐 DebugPanel) |

## 实现 bug 清单(intent 改文案绕不过,待应用修复)

1. **gateway 缺 /orchestrate 代理**(`services/gateway/src/main.py` 无 router,`routes/` 无 orchestrate.py)→ 前端 POST /api/orchestrate → gateway:8000/orchestrate → 404;后端 orchestrator:8001/v1/orchestrate 通。**修复**:gateway 加 `routes/orchestrate.py` 代理(POST /orchestrate → ORCHESTRATOR_API/orchestrate)+ main.py 注册 prefix=/orchestrate
2. **canvas WS B1 修复对重连无效**(`services/orchestrator/src/api/routes/canvas.py:182` 初始连接 `replay(session_id, ws)` 不读 after_event_id query param)→ wsClient 重连全量重放(非断点续传)。B1 的 after_id 只在 replay **命令**路径生效(canvas.py:224-226),wsClient 重连走 query param 不发命令。**修复**:canvas.py:182 读 `after_event_id=ws.query_params.get("after_event_id")` 传入 replay
3. **execution-history UI 不存在**(/memory 无 history tab,只有 debug;DebugPanel 扁平 timeline,无 Run 分组/条形图/错误计数/回放按钮)→ 需前端补 UI 或 intent 对齐 DebugPanel(本次 intent 已重写对齐)
4. **create-agent UI 无创建表单**(/agents 只一键按钮 handleCreate 硬编码 name/model)→ 需前端补表单(name/model/system_prompt 输入)或 intent 描述一键实际(本次 intent 已描述实际 + 标 NOT-WIRED)
5. **pitfall 末尾斜杠 404**(orchestrator `redirect_slashes=False`,GET /v1/pitfall → 404,需 /v1/pitfall/)→ intent 已加 / 绕过;根治可改 pitfall.py 路由或 redirect_slashes

## 与 agentic test 衔接(Phase 0/1)

- Phase 0:`test-run http://localhost:3000 docs/agentic-test/intents/P0/create-agent.md` → Stagehand 读 intent → 真实页面执行 → 截图+trace
- Phase 1:包成 MCP/skill,Claude Code 改完代码自动跑相关 intent(如改 orchestrate.py → 跑 multi-agent-orchestrate intent)
- intent.md 是**意图契约**(用户视角),代码改了若 intent 跑不通 = 回归(agentic test 价值)
