# Spec: TUI 交互打磨(第五轮:鼠标悬停 + 键盘焦点 + Control 完善)

> Date: 2026-07-15 | topic: tui-interaction-polish | base: 343b227 | Status: Draft
> 前序:四轮(TabBar + Home dashboard + 控件深度 + 页内鼠标 + WS 直连)| 配套 ADR:[[tui-interaction-polish]]

## 1. Background
四轮后 TUI 有鼠标点击 + WS 实时,但交互视觉缺(鼠标悬停无高亮、键盘焦点无统一框)+ Control 功能不全(orche 离线无反馈、flow 原语不在 Control、点击无 loading)。第五轮:交互打磨。

## 2. Goals (In-Scope)
- **鼠标悬停高亮**:`MouseCursor` 在交互元素(Control 按钮/Observe session/tab)上 → 该元素高亮(接 render + `MouseCursor.in_rect`,已有方法)
- **键盘焦点光标**:统一 focus indicator(App 加 `focus` 字段,Control 按钮/Flows flow/Observe session 键盘聚焦框;Tab/方向键切焦点)
- **Control 完善**:
  - orche `/health` 在线检测(预检 + 离线提示显示)
  - flow 原语入 Control(create/run flow 按钮,接 create_preset_flow/run_current_flow)
  - 点击 loading 反馈(按钮点击后短暂高亮/状态)

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法/数据结构不改(新增 UI 状态 hover/focus/loading + orche health fetch,业务方法 do_turn/do_spawn/create_preset_flow/run_current_flow 不动)
- gateway / observe 通配订阅 / 鼠标高级(拖拽 tab/右键菜单)

## 4. User Stories
- 鼠标悬停按钮/列表项 → 高亮(视觉反馈,知道可点)
- 键盘 Tab/方向键切焦点 → 聚焦框(键盘可见,无鼠标也能定位)
- Control 点 trigger → orche 离线提示 / 点击 loading 反馈 / flow create+run 按钮可用

## 5. Constraints (ADR)
- [ADR-1] 鼠标悬停高亮(MouseCursor.in_rect 接 render,按钮/列表项/tab hover 高亮)
- [ADR-2] 键盘焦点光标(统一 focus indicator,App focus 字段 + 键盘聚焦框)
- [ADR-3] Control 完善(orche /health 在线检测 + flow 原语入 Control + 点击 loading 反馈)
- [ADR-4] 业务/数据结构不改(延续红线;新增 UI 状态 + orche health fetch,业务方法不动)
- [ADR-5] verify 含 release

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + hover/focus/Control 完善(skeptic 验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 全做 A+B 已锁)

## 8. Defer 预判
- gateway 集中 / observe 通配订阅 / 鼠标高级(拖拽 tab/右键菜单)/ Home 复杂图表
