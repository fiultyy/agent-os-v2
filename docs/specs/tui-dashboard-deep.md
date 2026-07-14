# Spec: TUI Home dashboard + 控件深度接入(第二轮)

> Date: 2026-07-14 | topic: tui-dashboard-deep | base: c3fdc95 | Status: Draft
> 前序:tui-web-rebuild 第一轮(TabBar 4 tab 布局壳,Home 占位)| 配套 ADR:[[tui-dashboard-deep]]

## 1. Background
第一轮建了 TabBar 4 tab 布局壳(Home 静态占位)。第二轮:Home dashboard 真数据 + 已完成控件深度接入(ScrollView/HSplit/md_to_text 第 2 批基础控件,目前仅 widgets_demo 演示用,主 TUI 未消费)。

## 2. Goals (In-Scope)
- **Home dashboard 三块**(read-only 读 App 业务字段):
  - session 总览:harness 分组(claw/claude-code 各 N 个)+ 总数 + 多实例 ×N 标记
  - flow 状态:`flows` 里 running/completed/failed 计数
  - cursor session 摘要:当前 cursor session 的最近 turn_status / 事件数
  - 用 `position::percent_line` 显指标(如活跃 session 占比)
- **Observe tab 控件接入**:
  - `HSplit` resizable(session 树 | turn stream,拖分隔条改比例,复用 components/split.rs + ClickMap 命中分隔条)
  - `ScrollView`(turn stream 长内容滚动,复用 components/scrollbar.rs)
- **help 弹窗**:`md_to_text` markdown 渲染(替代当前纯文本,复用 components/markdown.rs)

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法(do_turn/do_spawn/refresh_flows/fetch_* 等)不改 —— **read-only 接入**(新增 Home 只读字段渲染 + Observe scroll/split 状态字段,不改业务逻辑)
- Agents/Canvas/Memory tab
- Canvas 用 AnchorGraph 画编排图
- 鼠标高级(拖拽 tab 重排 / 右键菜单)
- Home 复杂图表(仅文本+指标行)

## 4. User Stories
- 用户在 Home 看 session/flow/cursor 真实总览(数据驱动,非占位)
- 用户在 Observe 拖分隔条调 session 树|turn stream 比例;turn stream 长可滚(ScrollView)
- help 弹窗是 markdown 渲染(标题/列表/代码高亮)

## 5. Constraints (ADR)
- [ADR-1] Home 三块 dashboard,read-only 读 App 业务字段(sessions/instances/flows/cursor/turn_status),用 position::percent_line 显指标
- [ADR-2] Observe tab:HSplit resizable(session|turn stream)+ ScrollView(turn stream 滚动)
- [ADR-3] help 弹窗 body 改 md_to_text(markdown 渲染)
- [ADR-4] 业务机制不改(read-only 接入,延续第一轮 ADR-3)
- [ADR-5] verify acceptance 必含 `cargo build --release`(第一轮 release gap 教训:debug 绿≠release/CLI 绿)

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿(38 不破)+ `--dump` 显示 Home 三块真数据 + Observe HSplit + help markdown + state.rs 业务方法 git diff 为空

## 7. Open Issues
(无 —— Q1/Q2 已锁定)

## 8. Defer 预判
- Agents/Canvas/Memory tab(下轮)
- Canvas 用 AnchorGraph 编排图(下轮)
- state.rs 业务 tab 化(下轮)
- 鼠标高级交互(下轮)
- Home 复杂图表/sparkline(下轮)
