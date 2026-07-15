# Spec: TUI 弃 polling,WS 直连 + REST 辅助(第四轮)

> Date: 2026-07-15 | topic: tui-ws-direct | base: cc05fbb | Status: Draft
> 前序:tui-web-rebuild + tui-dashboard-deep + tui-intra-mouse(三轮 TUI rebuild)| 配套 ADR:[[tui-ws-direct]]

## 1. Background
TUI 当前 Tick 同步 REST 轮询(`fetch_claw_events`/`refresh_flows`)阻塞渲染线程,ratatui 5ms 渲染优势被 REST 往返吃掉(高性能发挥不出)。observe 已有 WS(`/ws/subscribe` 按 `(harness_type, session_id)` 订阅,或che turn+flow 事件经 `ObserveEmitter → observe /ws/ingest → broadcast`)。第四轮:TUI 弃 polling,WS 直连 observe 收 turn+flow 实时,REST 辅助(触发/初始)。

## 2. Goals (In-Scope)
- **WS 直连 observe /ws/subscribe**:订阅 cursor session(turn 实时)+ 每 tracked flow(`("flow", flow_id)`,flow 实时)
- **tungstenite + WS manager 线程 + mpsc channel**(基于同步 ratatui,不引 tokio;WS 线程收 → channel → main loop `try_recv` 非阻塞)
- **弃 polling**:Tick 不再 `fetch_claw_events`/`refresh_flows`(WS 推送更新 `app.events`/`app.flows`)
- **REST 辅助保留**:触发类(`trigger_turn`/`create_session`/`spawn_instance`/`create_flow`/`run_flow`)+ 初始 `fetch_sessions`
- **多 WS 连接**(observe 单 key 订阅 → cursor session + 每 flow 各一 WS;切换 cursor 重订阅 session WS,create flow 开 flow WS)

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法/数据结构不改(`do_turn`/`do_spawn`/`create_preset_flow`/`run_current_flow`/`fetch_*` + `sessions`/`flows`/`events`/`instances` 字段不变)—— **只传输层**(polling→WS)
- gateway 集中(忽略,ADR-9 本地优先)
- 鼠标交互增强(悬停高亮)/ 键盘焦点光标 / Control tab 完善(下轮)
- observe 改通配订阅(不改 observe,多 WS 连接)

## 4. User Stories
- TUI 渲染不被 REST 阻塞(WS 后台收事件,main loop try_recv 非阻塞)→ 发挥 ratatui 高性能
- turn/flow 事件实时推送(弃轮询,低延迟)
- 触发类操作仍 REST(trigger_turn/create_flow/run_flow 一次性)

## 5. Constraints (ADR)
- [ADR-1] WS 直连 observe,turn+flow 全 WS,tungstenite + 线程 + channel(不引 tokio)
- [ADR-2] REST 辅助保留(触发类 + 初始 fetch_sessions),不弃
- [ADR-3] 业务/数据结构不改(read-only 传输层换,polling→WS)
- [ADR-4] 多 WS 连接(observe 单 key 订阅)+ WS manager 线程动态 add/remove(cursor 切换/flow create)
- [ADR-5] verify 含 `cargo build --release`(沿用)

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + WS 连 observe 收 turn/flow(单测/集成)+ Tick 不 REST 轮询(grep 核验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 turn+flow 全 WS / Q2 tungstenite+线程+channel 已锁)

## 8. Defer 预判
- 鼠标交互增强(悬停高亮 + 点击反馈)
- 键盘焦点光标(focus indicator)
- Control tab 完善(orche 在线检测 + flow 原语入 Control)
- gateway 集中(需复议 ADR-9)
- observe 通配订阅(简化多 WS)
