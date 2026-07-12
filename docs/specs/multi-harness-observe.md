# Spec: multi-harness-observe(多 harness turn 观测操作台)

Date: 2026-07-12
Status: Draft
ADR: docs/adr/multi-harness-observe.md
Iteration Base: 087886d63c7621d0cefe30166526c617dc236560

## 1. Background
当前 turn 观测(canvas/events + emitter + event_store)耦合在 orchestrator 进程内,只能观测 agent-os-v2 自身执行(chat.py 的 `_canvas_emit`)。用户要观测**成熟 harness**(claude code / openclaw)的 turn,并在一个统一前端里交互 + 切换 session 查看。必须把观测抽成**独立服务**(observe-service),经通用协议接入任意 harness,前端成**统一操作台**(观测 + 驱动 + 真实 session 管理)。agent-os-v2 自身降级为"可接入的骨架示例"。

## 2. Goals (In-Scope)
- 新建 **observe-service** 独立进程(`services/observe/`,端口 8002):统一 turn 事件协议(语言无关 schema,泛化 canvas.events 加 `source`/`harness_id`)+ 真实 **session registry**(复合键 `(harness_type, session_id)`,sqlite 持久化,创建/列表/切换/replay)+ WS ingest(gateway 推)+ WS subscribe / REST replay query(前端拉)+ 交互路由(前端消息 → 对应 gateway)
- **claude-code-gateway**:tmux session 承载 + stream-json 驱动;真 claude code turn 观测 + 交互 + 真实 session(tmux + `--resume`)
- **openclaw-gateway**:ACP 长连接;真 openclaw turn 观测 + 交互 + 真实 session(接 openclaw session 机制)
- **agent-os-v2-gateway 骨架**:orchestrator 进程内 observe client,替换 chat.py `_canvas_emit`,证明 observe-service 可接 agent-os-v2
- **前端操作台**:harness 选择 + session 列表/切换 + 对话交互 + turn 实时观测 + 历史 replay
- **mock gateway ×2**:模拟 claude-code-like / openclaw-like 正常 turn 回调序列,验证 observe-service + session 切换

## 3. Out-of-Scope (Non-Goals)
- **记忆服务 REST 化** — 本迭代不动 memory 能力(保持进程内 `_state.memory_service`);REST 化 defer 下迭代
- 真 claude code / openclaw 之外的**第三方 harness 接入** — 协议/schema 预留,不做实际 adapter
- **token.delta 高频流式观测** — 只做 turn 边界事件(started/tool_call/tool_result/completed),高频 token 流 defer
- observe-service 的**认证授权 / 横向扩展** — MVP 单实例无 auth
- **agent-os-v2 完整迁移到 observe-service** — T4 只做骨架(验证可接 + 主路径不回归),完整迁移是后续

## 4. User Stories / Scenarios
- As a 开发者,我想在一个前端**同时观测** claude code 和 openclaw 的 turn,以对比/切换查看不同 harness 执行
- As a 开发者,我想经 observe-service 前端**发消息驱动** claude code / openclaw,而不必分别开各自 CLI
- As a 开发者,我想**创建/切换多个真实 session**(每 harness),管理多对话
- As a 开发者,我想 **replay 历史 session** 的 turn,回看执行
- GIVEN claude code turn 发生 WHEN gateway 解析 stream-json THEN observe-service 实时收到完整 turn 序列 AND 前端显示
- GIVEN 多 harness 多 session WHEN 切换 THEN 前端拉该 session replay + 订阅实时 AND 事件互不串

## 5. Constraints
- observe-service **独立进程**,经 WS 协议接 gateway(非进程内 import)[ADR-1, ADR-2]
- turn 事件 schema **语言无关**(泛化 canvas.events,加 `source`/`harness_id`)[ADR-6]
- claude-code-gateway = **tmux 承载 + stream-json**(非 capture-pane / hook)[ADR-3]
- openclaw-gateway = **ACP 长连接**[ADR-4]
- session **复合键** `(harness_type, session_id)` + 真实持久化 [ADR-5]
- agent-os-v2 chat.py 主路径 `/execute` **零回归**(16/16 基线);替换 `_canvas_emit` 必须 None-guard + fire-and-forget [ADR-7]
- P2 执行 = **longline-ultracode + tmux 多实例协作**(含 openclaw 目录实例写 T3)[ADR-8]

## 6. Acceptance(关联编排图节点)
- [ ] **Node A**(T1):observe-service 协议/session/ingest/query 端到端通(独立进程)
- [ ] **Node B**(T2/T3/T4):claude-code-gateway 真 turn+交互+session;openclaw-gateway 真 turn+交互+session;agent-os-v2 骨架接入且主路径不回归
- [ ] **Node C**(T5/T6):前端操作台全功能;mock 验证 2 harness 序列 + session 切换
- [ ] **P3 Regression**:all intents(含新 observe intent)+ observe-service 端到端
- [ ] **ADR Compliance**:8 条全 upheld

## 7. Open Issues
- ~~O1: stream-json 持续多轮对话机制(`-p` 一次性 vs `--resume`)~~ → **defer P2 T2 探查**,执行时记录变更进 ADR-3
- ~~O2: openclaw ACP 接入点(token/binding/channel 具体接口)~~ → **defer P2 T3 深探** `~/tools/openclaw` 源码,记录变更进 ADR-4
- O3: observe-service 端口 / db 路径 → **P1 定**:端口 `8002`,WS ingest `/ws/ingest`,db `data/observe_events.db`(sqlite);容器化适配 P2

(O1/O2 为技术探查型,不阻塞 P1 锁定;O3 P1 已定。§7 清空 → 可锁定。)

## 8. Defer 预判
- 记忆服务 REST 化(下迭代)
- token.delta 高频流(下迭代)
- observe-service auth / 横向扩展(MVP 后)
- 真 claude code / openclaw 之外 harness(协议预留,按需)
- T2 stream-json 多轮若不可行 → 降级 hook 或 capture-pane(ADR-3 变更记录)
- T3 ACP 若接入成本过高 → 降级 openclaw hook / 输出解析(ADR-4 变更记录)
- agent-os-v2 完整迁移到 observe-service(T4 骨架之后)
