# ADR: tui-interaction-polish
Date: 2026-07-15 | Status: Active

## ADR-1: 鼠标悬停高亮(MouseCursor.in_rect 接 render)
Status: Accepted | Date: 2026-07-15
Context: MouseCursor 当前仅指示器(黑底黄字高亮鼠标位置),无悬停交互(光标在按钮/列表项上时该元素不高亮,用户不知可点)。
Decision: render 时,对交互元素(Control 按钮/Observe session 项/tab),用 `MouseCursor.in_rect(element_rect)` 判断光标是否在其上 → 是则该元素 hover 高亮(加边框/反色/亮度)。复用 components/mouse.rs MouseCursor.in_rect(已有)。
Alternatives: 无(已有 in_rect,接 render 即可)。
Consequences: render 每帧检查交互元素 in_rect(少量,O(n) n=按钮/项数)。hover 高亮样式(reversed/边框)。
Constrains: [T1]

## ADR-2: 键盘焦点光标(统一 focus indicator)
Status: Accepted | Date: 2026-07-15
Context: 键盘交互(Tab/方向键)无统一焦点框(Control 按钮/Flows flow 无键盘聚焦态;TabBar 有 highlight、Observe session 有 ▸+bgBlue,但不统一)。
Decision: App 加 `focus: FocusTarget` 字段(enum:TabBar/ControlButton(idx)/ObserveSession/FlowsFlow(idx))。键盘 Tab/方向键切 focus。render 时 focus 元素加聚焦框(边框/反色/▶ 标记)。统一所有 tab 的键盘焦点视觉。
Alternatives: 各 tab 自管 focus —— 不统一,用户难定位。
Consequences: App 加 focus 字段(UI 状态)。键盘 handler 加 focus 切换。render 各 draw_* 加 focus 高亮。
Constrains: [T1]

## ADR-3: Control 完善(orche health + flow 入 Control + 点击 loading)
Status: Accepted | Date: 2026-07-15
Context: Control tab 或che 离线无反馈(点 trigger REST 失败无提示)+ flow 原语在 Flows tab(键盘)+ 点击无 loading。
Decision: (1) orche `/health` 预检(App 加 `orche_online: bool`,周期/触发时 fetch /health,离线显示提示);(2) Control 加 flow 按钮(create Chain/Branch/DAG + run,接 create_preset_flow/run_current_flow);(3) 点击 loading 反馈(App 加 `last_action: Option<(Instant, &str)>`,按钮点击后短暂高亮 ~500ms)。
Alternatives: 无(完善 Control 功能)。
Consequences: 新增 fetch_orche_health(REST /health,新 fetch,非业务方法)。flow 按钮调已有 create_preset_flow/run_current_flow(业务方法不改)。loading 是 UI 状态。
Constrains: [T2]

## ADR-4: 业务/数据结构不改(延续红线)
Status: Accepted | Date: 2026-07-15
Context: 延续前四轮"业务机制不改";交互打磨是 UI 层 + orche health fetch。
Decision: state.rs 业务方法(do_turn/do_spawn/create_preset_flow/run_current_flow/fetch_*/set_sessions 等)+ 数据字段(sessions/flat/events/instances/flows/flow_cursor)不改。新增:UI 状态(focus/hover/loading/orche_online)+ fetch_orche_health(新 REST /health,非业务)+ render/handler 改。skeptic git diff 核验业务方法未改。
Alternatives: 业务改 —— defer。
Consequences: 红线守住(业务方法 git diff 空)。
Constrains: [T1, T2]

## ADR-5: verify 含 cargo build --release
Status: Accepted | Date: 2026-07-15
Context: 沿用 feedback-rust-cli-release-verify。
Decision: skeptic.acceptance + general_test 必含 `cargo build --release --bin v2-tui-rs`。
Constrains: [T1, T2]
