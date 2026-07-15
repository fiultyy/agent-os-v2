# ADR: Control v2 照 codex 重做 chat+input

Date: 2026-07-15
Status: Active
Topic: control-v2
Iteration base: ca85a78

## ADR-1: textarea 输入(无按钮,动作走键盘)
Status: Accepted
Context: Control 输入栏原是单行 prompt + 8 按钮(trigger/spawn/refresh/rawexec/chain/branch/dag/run),按钮与输入无关、抢占字母键,偏离「输入平面」目标。
Decision: 照抄 openai/codex `bottom_pane/textarea/` 的多行 textarea(bordered/光标/编辑/换行/粘贴)作输入;删 8 按钮;动作(trigger/spawn/refresh/rawexec/flow)走键盘。
Alternatives: 保留按钮(否——与输入无关、占空间);单行 prompt(否——codex 效果是多行 textarea)。
Consequences: 需新 textarea 组件 + 编辑键路由;输入历史/粘贴/@mention 一并照抄。
Constrains: [T-textarea, T-state]

## ADR-2: footer = 按键提示 + 状态(取代 StatusBar/tab 行状态)
Status: Accepted
Context: 原右区有独立 2 行 StatusBar(orche/session/last);codex 把状态放 footer(输入框下,与按键提示同行)。
Decision: 照抄 codex `footer.rs`:输入框下一行 = 按键提示(↵send·esc·↑hist)+ 状态(orche●/session/last),宽度自适应折叠。删独立 StatusBar。
Alternatives: 状态并入顶 tab 行(point 4 原案)——否,codex 放 footer 更贴参考。
Constrains: [T-footer, T-control, T-render]

## ADR-3: 顶栏 i(props 弹窗)+ ×(quit)
Status: Accepted
Context: props 原是常驻 tab(占空间);需快速退出入口。
Decision: 主顶 TabBar 右侧加 `i`(开 props modal Popup)+ `×`(quit_requested→run loop 退出)。
Constrains: [T-state, T-render]

## ADR-4: 左侧折叠树
Status: Accepted
Context: 左大纲分组需可展开选 session。
Decision: 每组 header(色块+名+count)点击 toggle(control_collapsed:HashSet);展开列 session(点选 cursor)。默认全展开。
Constrains: [T-state, T-render]

## ADR-5: 色块分区,去全边框
Status: Accepted
Context: region_block 全边框每区耗 2 行/列,浪费空间。
Decision: 去 region_block 全边框,用 bg 色块分区;ScrollView 加 bordered 开关(chat 无边框,textarea 保留薄边框)。
Constrains: [T-scrollbar, T-render]

## ADR-6: 不动后端(out-of-scope)
Status: Accepted
Context: jsonl 续 turn 已兼容(cc 走 claude --resume、claw 走 gateway sessions.send),全能力不阉割。
Decision: 本次纯 UI,不改 services/ 后端/session 存储。TUI 显示 observe 遥测,全保真按 e。
Constrains: [全局 out-of-scope]

## ADR-7: 组件 API 契约(并行 task 对齐)
Status: Accepted
Context: 节点 A 7 task 并行实现新组件,state/render 按契约集成。
Decision:
- `Textarea::new() -> Textarea`;`handle_key(KeyEvent) -> TextareaOp{Insert,Backspace,Left,Right,Up,Down,Home,End,Newline,Send}`;`render(f,area)`;`text()->&str`;`set_text/sel`。
- `InputHistory::new()`;`push(msg)`;`prev()/next() -> Option<&str>`。
- `Mentions::new(candidates:Vec<String>)`;`trigger()/open()`;`render_popup(f,area)`;`select()->Option<String>`。
- `Footer::render(f,area,hints:&[&str],status:Vec<Span>)`(宽度自适应)。
契约由节点 A 各组件实现;节点 B 集成时对齐;mismatch 由 skeptic/fix 收敛。
Constrains: [T-textarea,T-history,T-mentions,T-footer,T-state,T-render]
