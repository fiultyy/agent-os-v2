# Spec: Control 充实 + Observe→Control 跳转 + Observe 卷轴(第八轮)

> Date: 2026-07-15 | topic: tui-control-enrich | base: b2b7dc6 | Status: Draft
> 前序:七轮(含 VerticalStack N-pane 可扩展)| 配套 ADR:[[tui-control-enrich]]

## 1. Background
第七轮 VerticalStack N-pane 可扩展堆叠。第八轮:利用可扩展(push pane)加 flow/属性区 + Observe→Control 跳转(Observe 观测总览 → Control 详细查看)+ Observe tab 改卷轴滚动(日志风格总览)。

## 2. Goals (In-Scope)
- **flow/属性区**(Control 右堆叠 VerticalStack push pane):flow 区(flow lane/DAG)+ 属性区(cursor session 详情/事件统计)。验证 VerticalStack 真扩展(push pcts 加 pane)。
- **Observe→Control 跳转**:点 Observe session(或事件)跳 Control(跨 tab,cursor 同步 = 选中 session)
- **Observe 卷轴 UI**:Observe tab 改卷轴滚动风格(所有 session 事件流连续滚动,总览;WS 数据源不变,observe service 不改)

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法/数据结构不改(延续红线)
- observe service webhook(仅 TUI UI,service 不改;webhook defer)
- 鼠标高级(拖拽 tab/右键)/ Home 复杂图表

## 4. User Stories
- Control 右堆叠有 flow 区 + 属性区(push pane,VerticalStack 真扩展)
- Observe 卷轴看所有事件流(日志总览);点某 session → 跳 Control 详细查看+控制
- Observe 观测总览 / Control 详细(用户之前提"Observe 具体查看会跳 Control")

## 5. Constraints (ADR)
- [ADR-1] flow/属性区 push pane(VerticalStack pcts 加,Control 右堆叠 4 区:对话/输入/flow/属性)
- [ADR-2] Observe→Control 跳转(点 Observe session → panel=Control + cursor 同步)
- [ADR-3] Observe 卷轴 UI(Observe tab 卷轴滚动,所有 session 事件流;WS 不变,observe service 不改)
- [ADR-4] 业务/数据结构不改(延续红线)
- [ADR-5] verify 含 release

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + 3 主题(skeptic 验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 A+B+C + Q2 仅 TUI UI 已锁)

## 8. Defer 预判
- observe service webhook · 鼠标高级(拖拽/右键)· Home 复杂图表
