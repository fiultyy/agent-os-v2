# Spec: TUI 页面内鼠标交互接入(第三轮)

> Date: 2026-07-14 | topic: tui-intra-mouse | base: f5328f1 | Status: Draft
> 前序:tui-web-rebuild(第一轮 TabBar 布局壳)+ tui-dashboard-deep(第二轮 Home+控件深度)| 配套 ADR:[[tui-intra-mouse]]

## 1. Background
前两轮接了 TabBar 鼠标切 tab + Observe 分隔条拖拽,但**页面内交互元素无鼠标点击**(只键盘):Control 的 trigger/spawn/refresh/raw-exec 按钮、Observe 的 session 列表项都只能键盘操作。ClickMap 在主 TUI 0 处使用(仅 widgets_demo 演示)。第三轮:ClickMap 注册页面内交互元素,鼠标点击触发对应操作。

## 2. Goals (In-Scope)
- **Control tab 按钮**:`trigger`(t)/`spawn`(s)/`refresh`(r)/`raw-exec`(e)→ ClickMap 命中 → 触发对应 `do_turn`/`do_spawn`/`fetch_sessions+set_sessions`(刷新)/`open_popup`(raw-exec)
- **Observe tab session 列表项**:点击选中 cursor(对应键盘 j/k 的 cursor_down/up,点行 → cursor=该行 + fetch_current)

## 3. Out-of-Scope(defer)
- Flows tab flow 选择/创建/运行鼠标(下轮)
- `state.rs` 业务方法不改(命中调已有 handler 对应方法,read-only)
- 鼠标高级(按钮 hover/拖拽/右键菜单)
- Agents/Canvas/Memory tab · Canvas AnchorGraph · state.rs 业务 tab 化

## 4. User Stories
- 用户在 Control 点 trigger/spawn/refresh/raw-exec 按钮触发操作(不再只能键盘 t/s/r/e)
- 用户在 Observe 点 session 列表项选中该 session(不再只能键盘 j/k)

## 5. Constraints (ADR)
- [ADR-1] Control 按钮 ClickMap 命中(trigger/spawn/refresh/raw-exec → 对应操作)
- [ADR-2] Observe session 列表项点击选中 cursor
- [ADR-3] 业务 read-only(命中调已有方法,不加业务)
- [ADR-4] verify 含 `cargo build --release`(沿用第二轮 ADR-5,跨项目 feedback memory)

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + dump + 鼠标点击 Control 按钮/Observe session 项逻辑(skeptic 验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 锁定 Control+Observe)

## 8. Defer 预判
- Flows flow 选择/创建/运行鼠标(下轮)
- 鼠标高级(hover/拖拽/右键菜单)
- Agents/Canvas/Memory tab · Canvas AnchorGraph · state.rs 业务 tab 化
