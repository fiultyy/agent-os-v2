# ADR: tui-control-enrich
Date: 2026-07-15 | Status: Active

## ADR-1: flow/属性区 push pane(VerticalStack 扩展验证)
Status: Accepted | Date: 2026-07-15
Context: 第七轮 VerticalStack N-pane 可扩展(pcts vec)。需验证真扩展(push pane 加区)。
Decision: Control 右堆叠 VerticalStack push pcts(从 [75,25] 对话+输入 → 加 flow 区 + 属性区,pcts 4 pane 如 [40,20,20,20] 或动态)。flow 区:flow lane(openclaw turn 多实例)+ flow DAG(当前 cursor flow 状态)。属性区:cursor session 详情(sid/harness/实例/事件数/last turn)。
Alternatives: 不加(VSplit 2-pane 够)—— Q1 选 push。
Consequences: VerticalStack 真扩展验证(push pane 不改架构)。
Constrains: [T1]

## ADR-2: Observe→Control 跳转(跨 tab cursor 同步)
Status: Accepted | Date: 2026-07-15
Context: 用户提"Observe 具体查看会跳 Control"。Observe 观测总览 / Control 详细查看+控制。
Decision: Observe tab 点 session(或事件)→ panel=Control + cursor=该 session(跨 tab 导航,cursor 同步)。ClickMap/键盘中 Observe session 项加"跳 Control"动作(如双击/Enter/特定键)。
Alternatives: 无。
Consequences: Observe→Control 跨 tab(cursor 同步,WS 重订阅 Control cursor session)。Observe 不需自己详细查看(跳 Control)。
Constrains: [T2]

## ADR-3: Observe 卷轴 UI(WS 不变,风格改)
Status: Accepted | Date: 2026-07-15
Context: 用户要 Observe 偏卷轴滚动(总览,与 Control 不同堆叠)。observe service 不改(Q2 仅 TUI)。
Decision: Observe tab(draw_stack/draw_observe)改卷轴滚动风格:所有 session 事件流连续滚动(日志总览,ScrollView 大卷轴),不分 session 树|turn stream HSplit。WS 数据源不变(observe service 不改)。点 session → 跳 Control(ADR-2)。
Alternatives: observe service webhook —— Q2 仅 TUI defer。
Consequences: Observe tab 重构(卷轴总览,非 HSplit)。draw_stack 改 draw_observe_scroll。
Constrains: [T3]

## ADR-4: 业务/数据结构不改(延续红线)
Status: Accepted | Date: 2026-07-15
Context: 延续前七轮红线。
Decision: state.rs 业务方法(do_turn/do_spawn/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)+ 数据字段(sessions/flat/events/instances/flows/flow_cursor)不改。新增:UI 状态(control_stack pcts 扩 4 pane)+ render(draw_control push + draw_observe 卷轴)+ handler(Observe→Control 跳转)。skeptic git diff 核验业务方法未改。
Consequences: 红线守住。
Constrains: [T1, T2, T3]

## ADR-5: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-15
Context: 沿用 feedback-rust-cli-release-verify。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。
Constrains: [T1, T2, T3]
