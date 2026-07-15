# ADR: tui-ws-direct
Date: 2026-07-15 | Status: Active

## ADR-1: WS 直连 observe,turn+flow 全 WS,tungstenite+线程+channel
Status: Accepted | Date: 2026-07-15
Context: TUI Tick 同步 REST 轮询阻塞渲染(高性能发挥不出)。observe 已有 /ws/subscribe(按 (harness_type,session_id) 订阅),或che turn+flow 事件经 ObserveEmitter → observe /ws/ingest → broadcast。
Decision: TUI WS 直连 observe /ws/subscribe,订阅 cursor session(turn)+ 每 tracked flow(("flow",flow_id),flow)。tungstenite 同步 WS + WS manager 独立线程 + mpsc channel(WS 线程收事件 → channel → main loop try_recv 非阻塞)。新依赖 `tungstenite`(纯 Rust WS,无 tokio)。弃 fetch_claw_events/refresh_flows 轮询(WS 推送更新 app.events/flows)。
Alternatives: (a) tokio+tokio-tungstenite 异步 —— 需改 ratatui 同步模型,激进(Q2 选 tungstenite+线程);(b) turn WS + flow REST —— Q1 选全 WS。
Consequences: 多 WS 连接(observe 单 key 订阅,ADR-4)。WS 断线需重连(fallback REST 可选,defer)。新依赖 tungstenite(Cargo.toml 加)。
Constrains: [T1, T2]

## ADR-2: REST 辅助保留(触发类 + 初始)
Status: Accepted | Date: 2026-07-15
Context: 弃 polling 但触发类/初始加载仍需 REST(WS 是被动收,不能触发动作)。
Decision: REST 辅助保留:触发类(trigger_turn/create_session/spawn_instance/create_flow/run_flow,一次性动作)+ 初始 fetch_sessions(session 列表,WS 不推 session 变化)。这些不轮询,按需调。
Alternatives: 全 WS(触发也 WS)—— observe 无 send 能力(ADR-4 驱动归 orche REST),触发必 REST。
Consequences: trigger_turn 等 REST 仍同步(但一次性,不阻塞渲染循环;可后续异步化 defer)。
Constrains: [T1, T2]

## ADR-3: 业务/数据结构不改(read-only 传输层)
Status: Accepted | Date: 2026-07-15
Context: 用户明确"基于当前业务能力和数据结构调整";传输层换不应动业务。
Decision: state.rs 业务方法(do_turn/do_spawn/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)+ 数据字段(sessions/flat/events/instances/flows/flow_cursor)不改。新增:WS client 字段(ws_manager/channel)+ WS 事件处理(WS message → 更新 app.events[key]/app.flows[i].status,同现有 fetch_events/refresh_flows 的效果)。Tick 改(不 REST 轮询,只 try_recv channel + UI 刷新)。
Alternatives: 业务 tab 化 —— defer。
Consequences: skeptic git diff 核验业务方法未改(红线)。WS 事件映射到现有 events/flows 结构(不改 schema)。
Constrains: [T1, T2]

## ADR-4: 多 WS 连接 + WS manager 动态 add/remove
Status: Accepted | Date: 2026-07-15
Context: observe /ws/subscribe 单 key 订阅(query params 单 (harness_type,session_id))。TUI 需订阅 cursor session + N flows = N+1 key。
Decision: 多 WS 连接(每 key 一个 tungstenite WS)。WS manager 线程管理动态集合:cursor session WS(切换 cursor → 关旧开新)+ 每 tracked flow WS(create flow → 开;flow 终态 → 可关或保留收历史)。所有 WS 事件 → 单 mpsc channel → main loop try_recv。不改 observe(单 key 订阅保持)。
Alternatives: (a) observe 改通配订阅 —— 不改 observe(用户"基于当前业务能力");(b) 单 WS + 消息订阅多 key —— observe handler 用 query params 不支持。
Consequences: 多 WS 线程/连接管理复杂度(WS manager 负责动态 add/remove + 重连)。flow WS 生命周期跟 TrackedFlow。
Constrains: [T1, T2]

## ADR-5: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-15
Context: 沿用第二轮 ADR-5 + feedback-rust-cli-release-verify。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。新依赖 tungstenite 需 release 编译通过。
Constrains: [T1, T2]
