# ADR: tui-dashboard-deep
Date: 2026-07-14 | Status: Active

## ADR-1: Home 三块 dashboard,read-only 读 App 业务字段
Status: Accepted | Date: 2026-07-14
Context: 第一轮 Home 是静态占位;第二轮要真数据 dashboard。App 已有 sessions/instances/flows/cursor/turn_status 字段(P1 Step1 核实)。
Decision: Home dashboard 三块——session 总览(harness 分组+多实例 ×N)+ flow 状态(running/completed/failed 计数)+ cursor session 摘要(最近 turn_status/事件数)。read-only 读 App 字段渲染,用 `position::percent_line(selected,total)` 显活跃指标。不改业务方法。
Alternatives: (a) 复杂图表(sparkline/bar)—— ratatui 0.28 有 Chart widget 但 Home 空间小,overkill;(b) 仅 session 概览 —— Q1 选三块。
Consequences: Home 数据随 Tick 刷新(复用现有 fetch_claw_events/refresh_flows 周期更新,不改这些方法)。draw_home 重写(第一轮占位 → 三块)。
Constrains: [T1]

## ADR-2: Observe tab HSplit resizable + ScrollView
Status: Accepted | Date: 2026-07-14
Context: Observe(draw_stack)当前固定 35/65 分栏(session 树|turn stream),turn stream 长内容截断无滚动。第 2 批控件 split.rs(HSplit/HSplit resizable)+ scrollbar.rs(ScrollView)已实现但主 TUI 未消费。
Decision: draw_stack 用 `HSplit`(components/split.rs)替代固定 Layout 分栏(session 树|turn stream,resizable);turn stream 用 `ScrollView`(components/scrollbar.rs)滚动。App 加 `observe_split: HSplit` + `observe_scroll: ScrollView` 状态字段(鼠标拖分隔条改 pct + 滚轮/键 scroll)。
Alternatives: (a) 保持固定分栏 —— Q2 选 resizable;(b) 仅 ScrollView 不 HSplit —— Q2 选两者。
Consequences: events handle_base_mouse 加分隔条拖拽命中(ClickMap)+ 滚轮喂 ScrollView.scroll_up/down。draw_stack 函数体改(第一轮 ADR-4 "draw_* 不重写" 是第一轮约束,本轮明确改 draw_stack 接入控件)。
Constrains: [T2]

## ADR-3: help 弹窗 md_to_text
Status: Accepted | Date: 2026-07-14
Context: help 弹窗(open_help)当前 body 是 Vec<String> 纯文本。md_to_text(components/markdown.rs)已实现,widgets_demo 演示过。
Decision: open_help 的 body 改用 `md_to_text(markdown 字符串)`(标题/列表/代码高亮)。state.rs Popup.body 是 Vec<String>,help 弹窗特殊处理(或 Popup 加 Text 字段)——实现选最小:open_help 直接构造 markdown 渲染的 Line 列表。
Alternatives: 保持纯文本 —— Q2 选 md。
Consequences: help 内容更可读(层级/代码块)。tui-popup 接受 Text body(已验证,widgets_demo 用过)。
Constrains: [T1]

## ADR-4: 业务机制不改(read-only 接入)
Status: Accepted | Date: 2026-07-14
Context: 延续第一轮"业务机制先不改动";Home dashboard / Observe 控件都是 read + render,不改业务逻辑。
Decision: state.rs 业务方法(do_turn/do_spawn/refresh_flows/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)不改。新增的是 UI 状态字段(observe_split/observe_scroll)+ render 层(draw_home/draw_stack 重写)。read-only 读 sessions/instances/flows/cursor。
Alternatives: 同时业务 tab 化 —— defer 下轮。
Consequences: skeptic 用 git diff 核验 state.rs 业务方法未改(同第一轮 ADR-3 红线)。
Constrains: [T1, T2]

## ADR-5: verify acceptance 必含 cargo build --release
Status: Accepted | Date: 2026-07-14
Context: 第一轮 skeptic acceptance 只 cargo build(debug),全局 CLI(~/.local/bin/v2-tui → release)未重编 → 用户跑 v2-tui 看到旧版("没变化")。
Decision: 本轮 skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`(不仅 debug)。subagent 实施后必须 release 编译 + 刷新全局软链 binary。
Alternatives: 改全局软链指向 debug —— 不行(release 性能)。
Consequences: 杜绝 debug 绿但 release/CLI 旧版的 gap。记入跨项目 feedback memory(Rust CLI 迭代 verify 铁律)。
Constrains: [T1, T2]
