# ADR: 流式编排引擎(streaming orchestration engine)
Date: 2026-07-23
Status: Active
Iteration base: 0f253cc(A2A mesh 收尾后)
设计文档: docs/a2a-streaming-orchestration-design.html(§7 撬棍)

> 4 节点:F1(native fork+fan-out+lineage)→ F2(async turn)/ F3(observe parent 链,dep F1)→ F4(e2e)。engine-first;TUI fork-tree 可视 defer。

## ADR-S1: engine-first,TUI fork-tree viz defer
Status: Accepted
Context: 流式编排 MVP = human-driven fork-explore。engine(fork 原语 + 异步执行 + lineage + observe)= 后端 Python;TUI fork 树可视 + 人控面(compare/select/steer)= Rust 控制面。两层分离。
Decision: 本轮做 engine(F1-F4 + e2e,orchestrator + observe 侧,API+observe 验证)。TUI fork-tree 可视 + 人控收敛 defer 下轮。
Constrains: [F1, F2, F3, F4]

## ADR-S2: native fork = 克隆 ModelMessages + parent_session_id lineage
Status: Accepted
Context: fork_session 现 native 404(cc 用 `--fork-session`;claw 501 stub)。native 是 A2A mesh substrate,需自己的 fork。
Decision: native fork = 深拷贝源 session 的 ModelMessages(`rec["messages"]`)→ `_build_native_session(messages=copy, agent_id=继承源)` → 新 session。`orch_sessions` 加 `parent_session_id` 列(migration,老行 null)。fork 继承源 agent_id(同 agent 探不同方向;**跨 agent fork = handoff,defer**)。
Constrains: [F1]

## ADR-S3: fork 原语 = 1→N fan-out + per-branch direction
Status: Accepted
Context: 现 fork 1:1。"fork → 异步探不同方向"要多路。
Decision: ForkReq 支持 `targets: list[{first_message}]`(空则回退单 first_message 向后兼容)。每 target 一 fork(克隆同一父 messages + 各自 direction)。本轮 native;cc 保持 1:1,claw stub 不动。
Constrains: [F1]

## ADR-S4: 异步 native turn(asyncio.create_task,镜像 cc fire-and-forget)
Status: Accepted
Context: native trigger_turn 现 `await agent.run()` 同步阻塞 → fork 不能并发探。cc 已 fire-and-forget(background task + observe 事件)。
Decision: native turn 包 `asyncio.create_task`,HTTP 立即返 `{status: started, tick_id}`;状态经 observe tick_started/tick_completed 事件流(非 HTTP 阻塞)。fork 多路 → 并发探。
Constrains: [F2]

## ADR-S5: observe parent 链 = 接通已有 branch 事件 + parent_session_id
Status: Accepted
Context: events.py 有未接线的 `branch_created`/`branch_merged` 事件(带 parent_branch_id/fork_tick_id);observe session/event 表无 parent 列。
Decision: fork 时 emit `branch_created`(parent_branch_id=source);ObserveEvent/observe_sessions 加 `parent_session_id`;fork 树可观测(parent→child)。
Constrains: [F3]

## 红线
- **R1**:workflow_engine 不碰(fork/async 是 session 层,非 workflow)。
- **R5**:observe 改动不引 memory_event_bus/_trigger_ingest/memory_service。
- **RK11**:本轮不新增 LLM 工具(fork 是 HTTP/人控原语,非 v2_ tool)→ n/a;若加工具守源码无 v2_ 前缀。

## defer(本轮外)
TUI fork-tree 可视 + 人控面(compare/select/steer/approve)/ 跨 agent fork(=handoff)/ ForkCapability(LLM 工具,agentic 入口)/ 异步 turn 结果回收 endpoint / cancel 分支。
