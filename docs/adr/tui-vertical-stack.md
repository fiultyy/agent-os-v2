# ADR: tui-vertical-stack
Date: 2026-07-15 | Status: Active

## ADR-1: N-pane VerticalStack 组件
Status: Accepted | Date: 2026-07-15
Context: 第六轮 VSplit 是固定 2-pane(F4 skeptic),加第 3 区需嵌套组合。需真 N-pane 可扩展堆叠(后续加 flow/属性)。
Decision: components/split.rs 新增 VerticalStack:Vec<u16> pcts(N pane 按比例垂直分)+ rects(area)->Vec<Rect>(N pane)+ separators(area)->Vec<Rect>(pane 间分隔条,供鼠标拖拽命中)+ drag(pane_idx,dy,area)(改 pcts[pane_idx]/pcts[pane_idx+1],clamp 10..=90 或按 pane 数自适应)。N>=2。垂直方向(pane 上下堆叠)。
Alternatives: (a) 嵌套 VSplit(组合可扩展但非组件级,F4 现状);(b) ratatui Layout 固定(不可拖拽)。
Consequences: VerticalStack 可扩展(push pct 加 pane)。drag 改相邻 pane pct。separators 供 ClickMap 命中拖拽。
Constrains: [T1]

## ADR-2: Control 右堆叠改用 VerticalStack
Status: Accepted | Date: 2026-07-15
Context: Control 右堆叠当前 VSplit(对话|输入 2-pane)。改 VerticalStack 真可扩展。
Decision: App.control_stack 从 VSplit 改 VerticalStack(pcts=[75,25],对话+输入)。draw_control 右堆叠用 VerticalStack.rects(替代 VSplit.rects)。鼠标拖拽改 VerticalStack.drag(pane_idx)(分隔条命中 separators)。后续加 flow/属性 = push pct(VerticalStack pcts vec 加)。
Alternatives: 保留 VSplit(F4 未解)。
Consequences: Control 右堆叠真可扩展(VerticalStack)。control_stack 字段类型改(VerticalStack 非 VSplit,UI 状态非业务)。
Constrains: [T2]

## ADR-3: 业务/数据结构不改(延续红线)
Status: Accepted | Date: 2026-07-15
Context: 延续前六轮红线。
Decision: state.rs 业务方法(do_turn/do_spawn/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)+ 数据字段(sessions/flat/events/instances/flows/flow_cursor)不改。新增:VerticalStack 组件(components/split.rs)+ control_stack 类型改 VSplit→VerticalStack(UI 状态)+ render(draw_control 右堆叠用 VerticalStack)+ handler(drag separators)。skeptic git diff 核验业务方法未改。
Consequences: 红线守住。
Constrains: [T1, T2]

## ADR-4: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-15
Context: 沿用 feedback-rust-cli-release-verify。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。
Constrains: [T1, T2]
