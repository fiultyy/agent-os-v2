# Spec: Orchestrate TUI
Status: Draft → (go 后 Locked)
Date: 2026-07-23
ADR: docs/adr/orchestrate-tui.md

## 1. Background
流式编排 engine(F1-F4:fork/async/lineage)已落地,缺"脸":TUI 看不见 fork 树,人无法在树上交互编排。本轮做可视 + 可光标交互的编排界面(设计文档 §7 撬棍)。

## 2. Goals (In-scope)
- 第4 tab Orchestrate:observe fork 数据 → Braille 树可视(anchor.rs)
- 光标跨层级导航 + 节点选中(产出 Selection)
- 人控原语抽象层(OrchestratePrimitive trait + registry)+ 首批 4 实例(fork / async-turn / open-events / cancel)
- 事件驱动树状态实时刷新(branch_created / tick_started / tick_completed)
- 后端 cancel 端点(止损异步 turn)

## 3. Out-of-scope
compare / select / steer / approve / merge 原语 · branch_merged emit · native agent catalog HTTP · orche list_sessions parent 字段 · observe parent_session_id 索引 · TUI fork-tree 之外的人控面

## 4. User Stories
- 人在 Orchestrate tab 看到 fork 树(parent→child),每节点显 agent_id + 状态符号(●active/✓done/⠋running/○idle)
- 光标移到某分支:按 f 从该分支再 fork,按 t 异步探,按 x 取消跑飞的异步 turn,Enter 看该 session 事件流
- fork/async/tick 事件实时刷新树节点状态

## 5. Constraints (ADR)
- [ADR-O1] 第4 tab,Flows 零改动 · [ADR-O2] lineage 走 observe · [ADR-O3] 复用 anchor.rs
- [ADR-O4] 原语抽象层 · [ADR-O5] 后端资源化 · [ADR-O6] cancel 协作式中断
- R1(workflow 零改动)/ R5(observe 只读)/ RK11(n/a)

## 6. Acceptance (编排图节点映射)
- W-A: 后端 cancel 端点 ✓ + 前端骨架(树+光标+刷新)✓
- W-B: 原语抽象层基础(trait+registry+空壳)✓
- W-C: 4 原语实例注册(fork/async/open/cancel)✓
- green_definition: 全节点 out(pass) + p3 回归 + adr 对照 + 全分支审查

## 7. Open Issues
(无)

## 8. Defer 预判
见 ADR defer 节。compare 可纯前端零后端加(bonus)。
