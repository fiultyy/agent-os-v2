# Spec: N-pane VerticalStack 可扩展堆叠(第七轮)

> Date: 2026-07-15 | topic: tui-vertical-stack | base: 611162c | Status: Draft
> 前序:六轮(含 Control Cursor 式,VSplit 2-pane 堆叠,F4 指出非 N-pane)| 配套 ADR:[[tui-vertical-stack]]

## 1. Background
第六轮 Control 右堆叠用 VSplit(2-pane 对话|输入)。P3 skeptic F4 指出 VSplit 是固定 2-pane,加第 3 区需嵌套组合(非组件级 N-pane)。第七轮:做真 **N-pane VerticalStack**(Vec<pct> 动态分区),Control 右堆叠改用,为后续加 flow/属性区打架构基础。

## 2. Goals (In-Scope)
- **N-pane VerticalStack 组件**(components/split.rs):
  - `Vec<u16>` pcts 动态分区(N pane 按比例垂直分 + 分隔条)
  - `rects(area) -> Vec<Rect>`(N pane Rect)
  - `separators(area) -> Vec<Rect>`(pane 间分隔条,供鼠标拖拽命中)
  - `drag(pane_idx, dy, area)`(改 pcts[pane_idx]/pcts[pane_idx+1],clamp)
  - cfg(test)自检(N=2/3 分区 + drag clamp + separators)
- **Control 右堆叠改用 VerticalStack**(替代 VSplit 组合):对话 + 输入 pcts=[75,25],App.control_stack 改 VerticalStack

## 3. Out-of-Scope(defer)
- `state.rs` 业务方法/数据结构不改(延续红线)
- flow/属性区(后续 push 到 VerticalStack pcts)
- Observe→Control 跳转 · Observe webhook · 鼠标高级

## 4. User Stories
- VerticalStack N-pane 动态:后续 push pane(加 flow/属性)不改架构(真可扩展)
- Control 右堆叠从 VSplit 2-pane → VerticalStack N-pane(可扩展基础)

## 5. Constraints (ADR)
- [ADR-1] N-pane VerticalStack 组件(Vec<pct> + rects/separators/drag,components/split.rs)
- [ADR-2] Control 右堆叠改用 VerticalStack(替代 VSplit,pcts=[75,25] 对话+输入)
- [ADR-3] 业务/数据结构不改(延续红线)
- [ADR-4] verify 含 release

## 6. Acceptance (节点映射)
- 节点 A:cargo build(debug **+ release**)0 error + cargo test 全绿 + VerticalStack N-pane 动态分区(skeptic 验)+ state.rs 业务方法 git diff 空

## 7. Open Issues
(无 —— Q1 N-pane VerticalStack 已锁)

## 8. Defer 预判
- flow/属性区(VerticalStack push pane)· Observe→Control 跳转 · Observe webhook · 鼠标高级(拖拽/右键)
