# ADR: tui-control-cursor
Date: 2026-07-15 | Status: Active

## ADR-1: Control 独立整合 Cursor 布局;Observe 保留
Status: Accepted | Date: 2026-07-15
Context: Control 现状简单(按钮+flow lane)。用户要 Cursor agent mode 式(左大纲/右对话/右多功能)。Observe 后续迭代 webhook 观测(卷轴滚动,不同堆叠);Observe 具体查看跳 Control。
Decision: Control 独立整合 Cursor 布局:左大纲(session list + 分组 + 属性)+ 右对话(turn stream)+ 右底输入(turn_msg + 按钮)。从 observe sessions/events 读(同 Observe 数据源,独立视图)。Observe tab 保留(后续 webhook 迭代)。
Alternatives: Control 吸收 Observe —— Observe 有独立 webhook 观测方向,保留分离。
Consequences: Control/Observe 都读 observe sessions/events(独立视图,不冲突)。后续 Observe→Control 跳转 defer。
Constrains: [T1, T2]

## ADR-2: 可扩展垂直分区堆叠组件
Status: Accepted | Date: 2026-07-15
Context: 右区需对话 + 底输入(本轮)+ 后续 flow/属性(下轮)。用户强调"可扩展垂直分区堆叠组件"。
Decision: 右区用可扩展垂直堆叠(本轮 2 区:对话 + 输入)。复用 components/split.rs VSplit(垂直 resizable,第四轮已实现)或新简单 VerticalStack(Vec<Constraint> 动态分区)。架构支持后续加区(flow/属性 push 到堆叠)。subagent 选实现(优先复用 VSplit,不够再 VerticalStack)。
Alternatives: 固定 Layout(不可扩展)—— 违"可扩展"。
Consequences: 右堆叠可扩展(后续加区)。本轮 VSplit(对话 | 输入)或 VerticalStack。
Constrains: [T1, T2]

## ADR-3: 左大纲 session list(分组 + 属性)
Status: Accepted | Date: 2026-07-15
Context: Cursor 左大纲 = session/chat 列表 + 分组 + 属性。
Decision: Control 左大纲读 app.sessions.sessions_by_harness(harness 分组:claw/claude-code/agent-os-v2 等)+ 每 session 属性(session_id 截断 / harness_type / 实例数 instance_count / 事件数 events.len)。点选切 cursor(ClickMap,复用第三轮模式)。当前 cursor 高亮。
Alternatives: 无。
Consequences: 左大纲 ClickMap register session 项(同 Observe draw_stack 模式)。cursor 切换 WS 重订阅(第四轮)。
Constrains: [T1]

## ADR-4: 右对话(turn stream)+ 底输入栏
Status: Accepted | Date: 2026-07-15
Context: Cursor 右 = 对话 + 输入。
Decision: 右主区 = cursor session turn stream(app.events[key],observe events,stack_event_line 渲染,ScrollView 滚动)。右底输入栏 = turn_msg 输入(显示当前 message)+ trigger/spawn/flow 按钮(复用第五轮 trigger_control_button(id),鼠标+Enter 共用)。
Alternatives: 无。
Consequences: Control 右对话复用 stack_event_line + ScrollView(Observe 同款)。输入栏按钮复用 trigger_control_button。
Constrains: [T2]

## ADR-5: 业务/数据结构不改(延续红线)
Status: Accepted | Date: 2026-07-15
Context: 延续前五轮"业务机制不改";Control 重设计是 UI 布局重构。
Decision: state.rs 业务方法(do_turn/do_spawn/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)+ 数据字段(sessions/flat/events/instances/flows/flow_cursor)不改。新增:UI 布局状态(control_split/control_scroll 或复用)+ render(draw_control 重写为 Cursor 式)+ handler(clickmap session 项 + 按钮复用 trigger_control_button)。skeptic git diff 核验业务方法未改。
Alternatives: 业务改 —— defer。
Consequences: 红线守住。
Constrains: [T1, T2]

## ADR-6: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-15
Context: 沿用 feedback-rust-cli-release-verify。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。
Constrains: [T1, T2]

## ADR-7: 对话区模块化封装(可复用子组件)
Status: Accepted | Date: 2026-07-15
Context: Control 对话区(Cursor 式)含多种元素,需模块化封装便于复用 + 后续 GUI 重构消费。
Decision: 对话区子元素封装为独立可复用模块(第 3 批基础控件,加 components/):
- InputBar:turn_msg 输入 + trigger/spawn/flow 按钮(底输入栏)
- StatusBar:orche health + session + last turn(顶部状态)
- TurnSeparator:turn 之间视觉分隔(tick_started 开新 turn 块)
- ToolCallBadge:tool_call/tool_result 视觉标识(⚒ TOOL▸ / ◷ TOOL◂)
- md 渲染:对话内容 markdown(复用 components/markdown.rs md_to_text,接入 tick_completed response)
各独立 render fn,draw_control 组合。可复用(后续 Observe/web GUI 消费)。
Alternatives: 内联渲染(不可复用)—— 违"模块化封装"。
Consequences: components/ 加对话控件(第 3 批)。子组件可复用。
Constrains: [T1, T2]
