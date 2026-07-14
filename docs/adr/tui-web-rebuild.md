# ADR: tui-web-rebuild
Date: 2026-07-14 | Status: Active

## ADR-1: TabBar 4 tab 对应 web 核心功能
Status: Accepted | Date: 2026-07-14
Context: TUI 需按 web 布局分页;web 有 7 页但第一轮聚焦核心(P1 Q1 用户选"仅核心 3+1")。
Decision: TabBar = `Home / Flows / Observe / Control`(4 tab)。Flows←现有 draw_flow,Observe←现有 draw_stack,Control←现有 draw_control,Home 占位。复用 `components/tabs.rs`(基础控件层已实现 + 对抗验证,含窗口分页/点击命中)。
Alternatives: (a) 全 7 tab —— 过广,第一轮只要基本布局;(b) 仅 3 现有 panel 无 Home —— 无对应 web home,不符"按 web 布局"。
Consequences: Agents/Canvas/Memory defer 到下轮。4 tab 在常规终端宽度不触发 TabBar 分页 `<`/`>`(窗口够宽)。
Constrains: [T1]

## ADR-2: 鼠标交互复用 components/mouse.rs
Status: Accepted | Date: 2026-07-14
Context: 需鼠标点击切 tab + 光标指示;不能引新依赖(0.28 orphan,社区 crate 锁 0.29+ 不可用)。
Decision: `ClickMap` 注册 TabBar 各 tab 区域,`MouseEventKind::Down(Left)` 命中 → `tabbar.hit()` → 切 tab;`MouseCursor` 帧末渲染(黑底黄字)。复用 `components/mouse.rs`(已实现 + crossterm ?1003h Moved 验证)。
Alternatives: ratatui-interact(0.30-locked,Rect 不兼容,不可用)。
Consequences: crossterm `EnableMouseCapture` 已在 main.rs 开启。tmux 下 Moved 可能稀疏(终端行为,非 bug)。
Constrains: [T1]

## ADR-3: 只改 UI 层,state.rs 业务机制不动
Status: Accepted | Date: 2026-07-14
Context: 用户明确"第一轮先完成基本布局,业务部分机制先不改动"。
Decision: 改动限 `render.rs`(draw 重构:TabBar + 主区按 tab 分发 + MouseCursor)+ `Panel enum`(扩 Home + 重命名 Flow→Flows/Stack→Observe,UI 状态非业务)+ `events.rs`(鼠标点击分发 + 键盘兼容)。`state.rs` 的业务方法(do_turn/do_spawn/refresh_flows/create_preset_flow/run_current_flow/fetch_* 等)**不动**。
Alternatives: 同时 tab 化业务(过广,违"第一轮基本布局")。
Consequences: Panel enum 重命名波及 render.rs 的 match + state.rs 的 label/next(这些是 UI 状态方法,允许改);业务数据方法(sessions/flows/events/instances 字段及操作)不动。skeptic 用 git diff 核验 state.rs 业务方法未改。
Constrains: [T1]

## ADR-4: 现有 draw_* 迁移不重写
Status: Accepted | Date: 2026-07-14
Context: Flows/Observe/Control tab 第一轮需有内容,现有 draw_flow/draw_stack/draw_control 已渲染真数据(observe events/flow DAG/control 栏)。
Decision: 现有 draw_flow/draw_stack/draw_control 函数体不动,仅 render.rs 的顶层 draw() 把它们分发到对应 tab(Flows→draw_flow, Observe→draw_stack, Control→draw_control)。Home 新增 draw_home 占位。
Alternatives: 重写 draw_*(违"业务机制不动",无谓风险)。
Consequences: tab 内容第一轮 = 现有 3 panel 内容 + Home 占位,零业务渲染重写。
Constrains: [T1]
