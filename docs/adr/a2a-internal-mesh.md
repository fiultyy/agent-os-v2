# ADR: A2A 内部 mesh(agent 可配置化管理 + 发现 + 消费)
Date: 2026-07-23
Status: Active
Iteration base: 6c69079

> 5 节点编排图见 `.claude/orchestrator-graph.json`。节点:A(card+catalog)→ B(LocalTransport)→ C(v2_a2a_call 工具)→ E(e2e);D(observe agent_id)与 A/B/C 并行,汇入 E。

## ADR-1: 方向 = Consume,内部 mesh 优先(外部 defer)
Status: Accepted
Context: AO2 持久化 agent(native/main)需"可配置化管理 + 发现"。A2A 给发现层(AgentCard)+ 委派(message/send);codebase 已埋伏笔(native_agent.py/agent_runner.py "A2A 铺路")。命名撞车:本仓"ACP"= Agent Client Protocol(IBM/openclaw,client→agent 控制)≠ A2A ≠ AgentNetworkProtocol 的 ACP。orche 现通信 3 gateway(openclaw WS / claude-code subprocess / native 进程内)无 ABC,唯一共享契约 = ObserveEvent;A2A 代码 = 零。
Decision: 用 A2A 做**内部 mesh**——native/main 自成 A2A 节点(serve card + 互发现 + 互消费)。外部 expose/consume defer。
Alternatives: (a) 先消费外部 A2A agent——缺真靶子,e2e 不自包含;(b) 复用 openclaw ACP 路——client→harness 控制,无发现层,不对题。
Consequences: 自包含 e2e(靶子=自己 agent);外部 = 同工具换 transport,代码零改。
Constrains: [A, B, C, D, E]

## ADR-2: 形态 = A2A-as-tool(B),非 gateway
Status: Accepted
Context: consume 落 AO2 两切法:(a) 第 4 个 harness gateway(改 5 个 handler 分支 + 建 ABC,重);(b) native agent 拿 `v2_a2a_call` 工具 mid-turn 委派(小,对齐"走 build_native_agent"伏笔)。
Decision: **B 工具形态**。native/main 在 turn 内经 `v2_a2a_call` 发现 + 调用兄弟。
Alternatives: (a) gateway 形态——架构一致但重,且语义是 orche 顶层控制,非 agent 委派。
Consequences: 不动 routes.py 主干;复用 ToolBridge(v2_ 前缀 + pitfall);填 main 真实缺口(SOUL.md 写"委派"但今天没工具)。
Constrains: [C]

## ADR-3: pydantic-ai = 执行引擎,card 从 AgentSpec 投影
Status: Accepted
Context: pydantic-ai Agent 是命令式构造(model+tools+prompt),A2A card 是声明式元数据——**不同构**。run()↔message/send、output_type↔DataPart、run_stream↔message/stream 天然对得上。
Decision: card 从 **AgentSpec**(声明式配置)投影,不从 pydantic-ai Agent 投。LocalTransport = 进程内 `build_native_agent(target).run()` 包成 message/send 响应。
Alternatives: 从 Agent 对象反推 card——Agent 无自描述字段,丢失。
Consequences: Node A 切法正确(card←AgentSpec);skill 粗粒度(profile L1 + tag)非 1:1 tool。
Constrains: [A, B]

## ADR-4: consumed agent = peer,非 subagent
Status: Accepted
Context: "工具消费 agent 发 turn"是否算 subagent?subagent 在 AO2 = 临时 + 剥能力 + 零 writer(R1)+ lifecycle 归父(workflow `_spawn_agent`)。
Decision: consumed agent = **对等 peer**:全套自己 capability(不剥),跑**自己 agent_id scope**(memory 写自己 scope)。R1(workflow 子 agent 零 writer)是另一条轴,不触发。
Alternatives: 当 subagent 剥能力——丢失 main 完整身份/记忆,违背消费具名 agent 的初衷。
Consequences: 核心不变量 = **scope 按 agent_id 隔离**(native 召回看不到 main 记忆,除非显式跨 scope);需验 MemoryWriter 按 agent_id scope 不串。
Constrains: [B, E]

## ADR-5: 传输可插拔;MVP 同步;task 生命周期 defer
Status: Accepted
Context: A2A 是协议非传输。进程内调用走 HTTP 是杀鸡牛刀。A2A task 有 input-required/working/cancel,pydantic-ai run() 无原生状态机。
Decision: **LocalTransport**(进程内直调,零网络)给兄弟;**HTTPTransport** 给外部(defer)。MVP 同步 send→completed。完整 task 生命周期(input-required/working/cancel)= 在 pydantic-ai 外包状态层,defer。
Alternatives: 内部也走 HTTP——多一跳网络,无谓。
Consequences: 内部 mesh 零网络开销;defer 列表 +1(A2A 完整 task 状态机)。
Constrains: [B]

## 红线(机械 grep,P3 全分支审查)
- **R1**:`workflow_engine.py` 无 MemoryWriterCapability(本迭代不碰 workflow)。
- **R5**:workflow 无 `memory_event_bus/_trigger_ingest/memory_service`;无 EngineeringDisciplineCapability import。
- **RK11**:工具注册名源码**无 v2_ 前缀**(`a2a_call`,ToolBridge 内部 `.prefixed("v2")`→运行时 `v2_a2a_call`)。

## defer
HTTPTransport / 外部 A2A consume / 外部 expose(公网 card + `/.well-known`)/ consumed persistent-session 跨调用续聊 / A2A 完整 task 状态机(input-required/working/cancel)/ turn-as-graph 专项 agent 进 mesh。
