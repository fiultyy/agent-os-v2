# ADR: Orchestrate TUI(fork 树可视 + 可光标交互编排界面)
Date: 2026-07-23
Status: Active
Iteration base: 7a16abe(stream engine F4 收尾)
设计背景: docs/a2a-streaming-orchestration-design.html(§7 撬棍:TUI fork 树 + 人控面)

## ADR-O1: 第4 tab「Orchestrate」,与 Flows 物理隔离
Status: Accepted
Context: fork session 谱系树 ≠ workflow flow DAG。Flows panel 是 flow engine(workflow 红线 R1)的视图。混在一起会模糊红线。
Decision: 新增第4 tab `Panel::Orchestrate`,fork 树独占全屏。Flows panel / flow engine 零改动。
Constrains: [W-A-T2, W-B-T1]

## ADR-O2: lineage 走 observe GET /sessions,客户端建树
Status: Accepted
Context: orche `GET /h/{type}/sessions` 不返 parent_session_id(缺字段);observe `GET /sessions?harness_type=` 返 parent_session_id+agent_id(完整)。
Decision: TUI 从 observe 拉全量 sessions,客户端按 parent_session_id 分组建 fork 树。不改 orche list_sessions(补字段 defer)。
Consequences: 全量拉取 + 内存建树。session 过千需加 observe parent 索引或 /children 端点(`# ponytail: 全量拉取客户端建树,session 过千加索引`)。
Constrains: [W-A-T2]

## ADR-O3: 复用 anchor.rs AnchorGraph(Braille Canvas)
Status: Accepted
Context: components/anchor.rs 已封装 Braille Canvas 画线/点/标签(AnchorGraph),但无 panel 使用;Flows panel 用纯文本 BFS。
Decision: fork 树渲染走 anchor.rs AnchorGraph(parent→child 连线),不新建画布、不倒退回纯文本。
Constrains: [W-A-T2]

## ADR-O4: 人控原语抽象层(OrchestratePrimitive trait + registry)
Status: Accepted
Context: fork/async/cancel 散装 key binding 会让后续补 conditional/retry/gate/merge 线性膨胀成 if-key-x 平铺。
Decision: 定义 `OrchestratePrimitive` trait(id/key/label/enabled(&Selection)/invoke(&Selection,&mut App))+ `App.primitives: Vec<Box<dyn>>` registry;footer hint 渲染 + key dispatch + 灰显从 registry 自动派生。fork/async-turn/open-events/cancel 首批实例。新原语 = impl + 注册,分发/hint/灰显零改。
Consequences: 抽象横切拓扑/流控/人机所有类(不只流控)。最薄 trait(5 字段),不预建 plugin/DSL/inventory。为让 W-C 四实例 parallel 不撞共享文件,W-B 预建四空壳 + mod.rs all() 框架,W-C 各填自己文件。
Constrains: [W-B-T1, W-C-T1, W-C-T2, W-C-T3, W-C-T4]

## ADR-O5: 后端不统一端点(REST 资源化)
Status: Accepted
Context: fork/turn/cancel/merge 本是 session/turn 资源的 RESTful 动作;`/orchestrate/{primitive}` 总端点是假统一、加间接层。
Decision: 后端保持资源化端点(POST .../fork、.../turn、.../turn/cancel)。共享 emit/task 辅助(cancel 复用 _async_turn_tasks)。统一只在 TUI 侧。
Constrains: [W-A-T1]

## ADR-O6: cancel = 补的后端原语(协作式中断)
Status: Accepted
Context: 异步 turn(_async_turn_tasks)跑飞无法止损;无 cancel 端点。compare 可纯前端(defer)。
Decision: `POST /h/{type}/sessions/{id}/turn/cancel`(body {tick_id})→ `_async_turn_tasks[tick_id].cancel()` + emit `tick_completed(status=cancelled)` + 从 dict 移除;tick_id 不在 dict 返 404。TUI 注册 CancelPrimitive(enabled 仅 running)。
Consequences: 协作式取消,能否干净打断 GLM httpx 请求不定(可能跑完丢弃结果)。MVP 验证门 = 端点返 cancelled + observe 收 cancelled 事件 + task 移除(诚实 scope limit,非漂绿)。
Constrains: [W-A-T1, W-C-T4]

## 红线
- **R1**: workflow_engine.py / Flows panel / flow engine 零改动(grep diff 为空)。
- **R5**: observe 只读 GET /sessions,不引 memory_event_bus/_trigger_ingest/memory_service。
- **RK11**: n/a(无新 LLM 工具,cancel 是 HTTP 非工具)。

## defer(本轮外)
compare(可纯前端 bonus)/ select / steer / approve / merge / branch_merged emit / native agent catalog HTTP / orche list_sessions 加 parent 字段 / observe parent_session_id 索引。
