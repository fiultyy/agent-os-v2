# ADR: tui-intra-mouse
Date: 2026-07-14 | Status: Active

## ADR-1: Control 按钮 ClickMap 命中
Status: Accepted | Date: 2026-07-14
Context: Control tab 有 trigger(t)/spawn(s)/refresh(r)/raw-exec(e)键盘交互,无鼠标按钮命中。ClickMap(components/mouse.rs)已实现,主 TUI 0 处使用。
Decision: draw_control 注册 ClickMap 区域(按钮 Rect,id="trigger"/"spawn"/"refresh"/"raw-exec");handle_base_mouse 的 Down(Left) 在 Panel::Control 命中 → 触发对应操作(do_turn / do_spawn / fetch_sessions+set_sessions 刷新 / open_popup raw-exec)。App 加 clickmap 字段(每帧 clear+register,参考 widgets_demo 模式)。
Alternatives: (a) 全 3 tab —— Q1 选 Control+Observe;(b) 仅按钮不 session —— Q1 含两者。
Consequences: Control 按钮鼠标可用。命中调已有方法(ADR-3)。
Constrains: [T1]

## ADR-2: Observe session 列表项点击选中 cursor
Status: Accepted | Date: 2026-07-14
Context: Observe session 列表项键盘 j/k 选中(cursor_down/up),无鼠标点击。draw_stack 左栏 session 树(List widget)。
Decision: draw_stack 注册 ClickMap(session 列表项 Rect,id=行号 idx);handle_base_mouse 的 Down(Left) 在 Panel::Observe 命中 session 项 → cursor=idx + fetch_current(对应键盘 cursor_down/up 的效果)。注意与 Observe 分隔条命中(bar)+ turn stream 区(ScrollView 滚)区分:先判分隔条(已有),再判 session 项,再 turn stream。
Alternatives: 仅 Control —— Q1 含 Observe。
Consequences: Observe session 鼠标点选。命中调 cursor 设置 + fetch_current(已有方法,ADR-3)。
Constrains: [T2]

## ADR-3: 业务 read-only(命中调已有方法)
Status: Accepted | Date: 2026-07-14
Context: 延续前两轮"业务机制不改";鼠标命中只调已有键盘 handler 对应方法。
Decision: state.rs 业务方法(do_turn/do_spawn/refresh_flows/create_preset_flow/run_current_flow/fetch_*/set_sessions/cursor_down/cursor_up/fetch_current 等)不改。新增:UI 状态字段(clickmap: ClickMap)+ render 层(draw_control/draw_stack 注册 ClickMap)+ events 分发(handle_base_mouse 命中调已有方法)。
Alternatives: 加新业务 —— defer。
Consequences: skeptic git diff 核验业务方法未改(红线)。
Constrains: [T1, T2]

## ADR-4: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-14
Context: 沿用第二轮 ADR-5 + 跨项目 feedback memory(feedback-rust-cli-release-verify):debug 绿 ≠ release/CLI 绿。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。subagent 实施后 release 编译 + 刷新全局软链 binary。
Constrains: [T1, T2]
