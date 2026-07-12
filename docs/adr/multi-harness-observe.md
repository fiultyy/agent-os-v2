# ADR: multi-harness-observe(多 harness turn 观测操作台)

Date: 2026-07-12
Status: Active
Iteration Base: 087886d63c7621d0cefe30166526c617dc236560

---

## ADR-1: observe-service 独立进程(多 harness 操作台)

Status: Accepted
Date: 2026-07-12

Context:
当前观测(canvas/events + emitter + event_store)耦合在 orchestrator 进程内,只能观测 agent-os-v2(chat.py `_canvas_emit`)。要观测成熟 harness(claude code / openclaw),必须把观测抽成独立服务,经协议接任意 harness,且前端要能"观测 + 驱动 + session 管理"三合一(不只是旁观)。

Decision:
新建 `services/observe/`(FastAPI 独立进程,端口 `8002`),三职责:① 统一 turn 事件汇聚(WS ingest);② 真实 session registry(创建/切换/持久化/replay);③ query(WS subscribe 实时 + REST replay 历史)+ 交互路由(前端消息 → 对应 gateway)。不 import orchestrator,不依赖执行引擎。

Alternatives considered:
- orchestrator 内协议层(同进程抽象)— 不真正拆服务,外部 harness 接入受限,违反"完整解耦"
- 观测留 orchestrator + per-harness adapter — 不满足"完整解耦两个服务"(观测/记忆)

Consequences:
observe-service 独立部署/扩展;agent-os-v2/claude code/openclaw 都作为客户端接入;chat.py 改跨进程推事件(经 observe client,见 ADR-7)。

Constrains: [T1]

---

## ADR-2: harness-gateway 统一模式(WS 长连接)

Status: Accepted
Date: 2026-07-12

Context:
多 harness(claude code/openclaw/agent-os-v2/mock)接入需统一协议,避免 observe-service 为每 harness 写特化逻辑。

Decision:
所有 harness 经一个 **gateway**(WS 客户端)长连接 observe-service 的 WS ingest endpoint(`/ws/ingest`),推**统一 schema** turn 事件。observe-service 不关心 harness 来源,只管 schema + session。gateway 形态可不同(进程内 client / 独立 wrapper),协议统一。新增 harness = 写新 gateway,observe-service 零改。

Alternatives considered:
- 每 harness 不同协议 — observe-service 复杂,违反解耦
- HTTP 无状态 ingest — 不支持实时双向(交互回路 + 实时观测)

Consequences:
observe-service 单一 WS ingest;gateway 们主动连上来;协议契约(schema spec 文档)是接入唯一约定。

Constrains: [T1, T2, T3, T4, T6]

---

## ADR-3: claude-code-gateway = tmux 承载 + stream-json

Status: Accepted
Date: 2026-07-12

Context:
claude code 是主力 harness,要可靠 turn 事件 + session 管理 + 交互。tmux capture-pane 解析 ANSI 脆弱;stream-json(`claude -p --output-format=stream-json`)结构化可靠。

Decision:
claude-code-gateway 在 **tmux session** 里以 **stream-json** 模式驱动 claude code。tmux 做会话承载 / session 切换 / `attach` backup view / 进程持久化;stream-json 做 turn 事件源(结构化解析,不碰 ANSI)。observe-service 前端交互 → gateway → tmux 里的 claude code。

Alternatives considered:
- 纯 tmux capture-pane — turn 边界靠解析 ANSI,脆弱
- hook 系统(PreToolUse/PostToolUse/Stop)— per-tool 需组合重建 turn,且当前大部分 hook 禁用
- 纯 stream-json 无 tmux — 缺 session 承载/切换/attach

Consequences:
tmux + stream-json 组合;**持续多轮对话机制(`-p` 一次性 vs `--resume`)P2 T2 探查确认**,记录变更;若 stream-json 多轮不可行 → 降级 hook/capture-pane(变更记录)。

**技术决策(2026-07-13 T2 实施)**:
- **stream-json 是 `-p` 一次性**：每次 `claude -p --output-format=stream-json` 执行一个 turn 后退出，不支持持续多轮。
- **持续多轮方案**：不用 `--resume`(需要交互式输入)，改用"每轮启动新 claude -p 进程"。tmux session 做承载(attach backup view)，每轮用户消息触发新 claude -p 实例。
- **tmux 管理**：用 tmux CLI(原生命令，无 libtmux 依赖)，session 命名 `claude-observe-{session_id[:8]}`。
- **解析器**：逐行解析 stream-json，映射 assistant.tool_use → tool_call、user.tool_result → tool_result、result → tick_completed。
- **验证**：smoke test 通过(gateway 发 4 事件序列，observe-service 正确接收)。

Constrains: [T2]

---

## ADR-4: openclaw-gateway = ACP 长连接

Status: Accepted
Date: 2026-07-12
**变更 (2026-07-13 T3 implementer)**: 技术决策确认为 **Python 自实现 GatewayFrame 协议**(候选 B),非 TS 桥进程。

Context:
openclaw 是开源 TypeScript harness(`~/tools/openclaw`,github.com/openclaw/openclaw),有 **ACP(Agent Client Protocol)协议层** + gateway。开源可改,可内置接入,比外挂可靠。memory(`recall-redesign-progress`)印证 openclaw 有 recall/session 机制。

Decision:
openclaw-gateway 接 openclaw **ACP / gateway 长连接**,取 turn 事件 + 发交互 + 真实 session(接 openclaw session 机制)。**技术实现**: Python 自实现 GatewayFrame 协议(JSON over WebSocket),不用 TS 桥进程。

Alternatives considered:
- **候选 A (TS 桥进程)**: 用 `@openclaw/gateway-client` — 缺:包是私有(private: true, 0.0.0-private),未 npm 发布,需 vendor 源码,多 TS 进程管理,跨语言协调。
- **候选 B (Python 自实现)**: 手写 GatewayFrame 协议 — 缺:需手写协议帧序列化;优:纯 Python,单进程,协议简单(JSON + WS),版本固定(v4),与 observe-service 同语言。
- tmux 外挂 openclaw CLI — openclaw 开源不必外挂,浪费 ACP 可靠源
- openclaw stdout 解析 — ACP 协议层更结构化可靠

**变更理由 (2026-07-13)**:
- `@openclaw/gateway-client` 确认为私有包,未 npm 发布,candidate A 需 vendor 源码
- 协议本身简单 (RequestFrame/ResponseFrame/EventFrame + JSON),手写成本低
- "必须真实跑通"要求下,Python 自实现更可控,单进程无跨语言协调

Consequences:
**Python 实现** (`services/observe/gateways/openclaw.py`):
- WebSocket 客户端连 ws://localhost:18789
- 协议版本 4,帧序列化/反序列化
- sessions.messages.subscribe 订阅
- 事件映射: ChatEvent (delta/final/aborted/error) → ObserveEvent (tick_started/tool_call/tool_result/tick_completed)
- 交互路由: observe /send → openclaw chat.send (STUB T3)
- 真实 session: sessionKey ↔ (harness_type="openclaw", session_id=sessionKey)
- Gateway 端口 18789,协议版本 4,认证逻辑待实现(T3 假定本地无 auth)

**技术坑记录**:
- `@openclaw/gateway-client` 包未 npm 发布,只能 vendor 或本地构建
- ChatEvent delta (token streaming) 本次 defer,未映射 token_delta
- tick_started 合成逻辑未实现(需 chat.send 拦截)
- 交互路由 send_message() 当前 STUB,需保持连接状态

Constrains: [T3]

---

## ADR-5: session 复合键 (harness_type, session_id) + 真实管理

Status: Accepted
Date: 2026-07-12

Context:
多 harness 多 session,需统一管理 + 切换。用户强调"**真实** session 管理"(非 mock,真创建/切换/持久化)。

Decision:
observe-service **session registry**,复合键 `(harness_type, session_id)` 唯一标识一个 session。真实持久化(sqlite `data/observe_events.db`),支持创建/列表/切换/历史 replay。前端按 harness 分组,切换 = 订阅该 session 实时流 + 拉 replay。harness 自身 session 机制(claude code `--resume` / openclaw session)经 gateway 桥接到 observe session。

Alternatives considered:
- 单一 session_id 命名空间 — 跨 harness 冲突
- 纯内存 session — 不持久,重启丢

Consequences:
registry 是 observe-service 核心;gateway 负责 harness session ↔ observe session 映射;前端 session 列表/切换 UI 依赖此模型。

Constrains: [T1, T2, T3, T5]

---

## ADR-6: 事件 schema 泛化 canvas.events

Status: Accepted
Date: 2026-07-12

Context:
现有 `orchestrator/src/canvas/events.py` 8 类(TickStarted/TokenDelta/ToolCall/ToolResult/TickCompleted/BranchCreated/BranchMerged)是 agent-os-v2 专用(字段耦合 GraphState)。需语言无关通用 schema 让多 harness 接入。

Decision:
**泛化** canvas.events:加 `source`/`harness_id` 字段,去 agent-os-v2 专用耦合(GraphState 引用)。schema 迁到 observe-service 协议层(Python 参考实现 + 语言无关 spec 文档,供 TS openclaw 自实现)。turn 边界事件类型保持:`tick_started` / `tool_call` / `tool_result` / `tick_completed`(token_delta 高频 defer)。

Alternatives considered:
- 全新 schema — 重复造轮,丢现有 8 类的成熟建模
- OpenTelemetry GenAI semantic conventions — 重,过度,本场景不需分布式 trace

Consequences:
canvas.events 迁出至 observe-service 协议层;agent-os-v2 chat.py 用泛化 schema 经 client 推送(ADR-7);claude code/openclaw gateway 按 schema 产生事件。

Constrains: [T1, T4]

---

## ADR-7: agent-os-v2 主路径零回归(红线)

Status: Accepted
Date: 2026-07-12

Context:
agent-os-v2 chat.py `/execute` 是主路径(16/16 intent 基线 / 32 run 0 error)。T4 替换 `_canvas_emit` 为 observe gateway client 不能破坏主路径。observe-service 是新依赖,可能不可达。

Decision:
T4 的 gateway client(orchestrator 进程内)替换 `_canvas_emit`,**必须 None-guard + fire-and-forget**(对齐现有 `_canvas_emit` 的 try/except 风格)。observe-service 不可达时**静默降级**(`logger.warning`,不 raise),不改 `final_state` / SSE 流 / `execution_complete` 语义。skeptic 重点查此红线。

Alternatives considered:
- 无(红线不可妥协,主路径稳定优先于观测完整性)

Consequences:
T4 是**骨架**(验证 observe-service 可接 agent-os-v2 + 主路径不回归),不要求 agent-os-v2 完整迁到 observe-service(后续);observe-service 故障域隔离,不影响 agent-os-v2 执行。

Constrains: [T4]

---

## ADR-8: P2 执行 = longline-ultracode + tmux 多实例协作

Status: Accepted
Date: 2026-07-12

Context:
大迭代(6 task / 3 节点),T2/T3/T4 三 gateway 可并行,但 T3 要改 openclaw 源码(`~/tools/openclaw`,跨项目目录),T2/T4 在 agent-os-v2。需并行加速 + 跨项目协作。

Decision:
P2 用 **longline-ultracode** skill 编排节点(每节点 implementer/skeptic/qa-test/fix-loop max 3);**多开 tmux claude code 实例并行执行**(含在 `~/tools/openclaw` 目录开实例写 T3,在 agent-os-v2 目录开实例写 T2/T4)。tmux 协作用 tmux-ctrl(信号文件协调 + context 隔离)。变更/补充记录进事实文档(docs/specs + docs/adr 变更段)+ memory。compact 恢复靠产物(Spec/ADR/编排图)+ `.claude/orchestrator-state.json`。

Alternatives considered:
- 单实例串行 — 慢,P2 时间不可接受
- workflow 引擎纯 subagent — 不能跨项目目录开独立 claude code 实例写代码

Consequences:
tmux 协作要 context 隔离(每实例只看自己 task 的 ADR 约束 + 文件)+ 信号文件协调(节点完成 `.done`);P1 产物是唯一事实源,subagent 不臆造;compact 后从产物 + state 恢复。

Constrains: [T1, T2, T3, T4, T5, T6]
