# Spec: Control 区 Cursor 式重新设计(第六轮)

> Date: 2026-07-15 | topic: tui-control-cursor | base: 9ba2dbf | Status: Draft
> 前序:五轮(TabBar + Home + 控件 + 页内鼠标 + WS + 交互打磨)| 配套 ADR:[[tui-control-cursor]]

## 1. Background
Control 现状:status bar + 8 按钮 + flow lane(简单堆叠)。用户要参考 **Cursor agent mode** 重新设计:左大纲(session list + 分组 + 属性)/ 右对话内容 / 右垂直多功能。Control 独立整合(Observe 保留——后续 webhook 观测,UI 卷轴滚动不同堆叠;Observe 具体查看跳 Control 做详细内容查看 + 控制)。

## 2. Goals (In-Scope)
- **左大纲区**:session list(observe `sessions_by_harness` 分组)+ 属性(session_id / harness_type / 实例数 / 事件数)+ 点选切 cursor
- **右主区**:对话内容(cursor session turn stream,observe events)
- **右底输入栏**:`turn_msg` 输入 + trigger/spawn/flow 按钮(Cursor 式:对话 + 输入)
- **⭐ 可扩展垂直分区堆叠组件**:本轮对话 + 输入 2 区,**架构支持后续迭代加功能区**(flow / 属性 / 等)到右堆叠

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法/数据结构不改(读 observe sessions/events + 现有业务方法 do_turn/do_spawn/create_preset_flow/run_current_flow)
- flow 区 / 属性区(后续迭代加到右堆叠)
- Observe webhook 观测(后续迭代,卷轴滚动 UI)
- Observe→Control 跳转交互(点 session 跳 Control)+ 鼠标高级 / gateway

## 4. User Stories
- Control 左大纲看 session list(harness 分组 + 属性),点选切 cursor(ClickMap)
- Control 右看 cursor session 对话(turn stream 实时,WS 已推)
- Control 底输入 turn_msg + trigger/spawn/flow 按钮(Cursor 式:对话 + 输入)
- 后续迭代可加功能区到右垂直堆叠(flow / 属性,架构预留)

## 5. Constraints (ADR)
- [ADR-1] Control 独立整合 Cursor 布局(左大纲 + 右对话 + 右底输入);Observe 保留(后续 webhook)
- [ADR-2] 可扩展垂直分区堆叠组件(本轮对话+输入 2 区,架构支持后续加区;复用 split.rs VSplit 或新 VerticalStack)
- [ADR-3] 左大纲 session list(observe sessions_by_harness 分组 + 属性:session_id/harness_type/实例数/事件数)
- [ADR-4] 右对话(cursor session turn stream)+ 底输入栏(turn_msg + trigger/spawn/flow 按钮,复用第五轮 trigger_control_button)
- [ADR-5] 业务/数据结构不改(延续红线;读 observe + 现有业务方法)
- [ADR-6] verify 含 release

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + Cursor 式布局(左大纲/右对话/底输入,skeptic 验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 独立整合 + Q2 对话+输入/可扩展堆叠 已锁)

## 8. Defer 预判
- flow 区 / 属性区(堆叠后续加)· Observe webhook 观测 · Observe→Control 跳转 · 鼠标高级(拖拽/右键)· gateway
