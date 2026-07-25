# ADR: axis2-hooks
Date: 2026-07-25
Status: Active
Base: 1867b95(axis1-cleanup 后)

## ADR-1: H1 扩 EventType 6 个 + Context + MemoryHook 方法
Status: Accepted
Date: 2026-07-25
Context: 现有 MemoryEventBus 的 EventType 有 10 个(SESSION_START/TURN_START reserved/TURN_END/PRE_COMPACT/SESSION_END/DELEGATE/INGEST/CONSOLIDATE/RECALL/CURATE),Tool/Stop/Submit/Subagent 生命周期 event 缺失;TURN_START 已定义但 reserved 未 fire。Tool 层 hook 硬编码(ToolExecutor guardrail.check 直调 executor.py:74/106),非 bus 注册。读代码验:bus 机制健壮(register/emit/priority/degradation),hook 接口 on_{event}(ctx) 需固定 *Context 类型。
Decision: 加 6 EventType:`TOOL_PRE`/`TOOL_POST`/`TOOL_POST_FAIL`/`TURN_SUBMIT`/`STOP`/`SUBAGENT_STOP`(TURN_START 已存在,接 fire 点即可)。加 Context 类(ToolContext/StopContext/SubagentContext;TURN_SUBMIT 复用 TurnContext)。MemoryHook base 加 6 no-op 方法(on_tool_pre/on_tool_post/on_tool_post_fail/on_turn_submit/on_stop/on_subagent_stop,子类按需 override)。
Alternatives:
- Claude 27 全 fire(含 Permission/PreCompact/Notification):Permission 无场景 defer(ADR-5),PreCompact/Notification AO2 无压缩/TUI 侧 defer
Consequences: bus 机制不变(register/emit/priority/degradation)。新 event 默认无消费者(fire 空跑,未来注册)。hooks.py base MemoryHook 加 6 no-op 方法。
Constrains: [A]

## ADR-2: H2 fire 点 5 处
Status: Accepted
Date: 2026-07-25
Context: 加了 event 要在生命周期点 fire 才有用。当前 ToolExecutor 硬编码 guardrail(无 fire),trigger_turn 无 SUBMIT fire,agent.run 无 STOP fire,workflow/a2a consumed 无 SUBAGENT_STOP fire。TURN_START reserved 未接 fire。
Decision: 5 fire 点:(1) ToolExecutor.execute:fire TOOL_PRE(前)→ run → TOOL_POST(成功)/ TOOL_POST_FAIL(timeout/exception);(2) trigger_turn(chat.py /h turn + harness/routes):fire TURN_SUBMIT(入口)+ TURN_START(agent.run 前);(3) agent.run 完成:fire STOP;(4) workflow_run/a2a_call consumed agent 结束:fire SUBAGENT_STOP。每点 fire + 对应 Context。
Alternatives: 仅 Tool 层 fire(Tool/PostFail),Turn/Stop/Subagent defer——但全 fire(决策)+ SubagentStop 对流式编排收口有用。
Consequences: 每工具调用/turn 多 event fire(observe async fire-and-forget,性能 OK)。fire 点 import bus(`_state.memory_event_bus`)。
Constrains: [B]

## ADR-3: H3 guardrail 注册式 + ToolExecutor 据返值(bus 不改)
Status: Accepted
Date: 2026-07-25
Context: ToolExecutor 硬编码 guardrail.check/check_output(executor.py:74/106),非 bus 注册。迁移到注册式需 guardrail 作 MemoryHook 注册 TOOL_PRE/TOOL_POST。bus.emit 不短路(返 last non-None),guardrail deny 要 ToolExecutor 据返值决策。
Decision: guardrail 注册为 MemoryHook(SYSTEM priority 早跑),on_tool_pre(ctx) 返 {allow,reason}(input check),on_tool_post(ctx) 返 {allow,reason}(output check)。ToolExecutor execute:fire TOOL_PRE → emit 返聚合决策(非 None 且 allow=False → blocked 返);run;fire TOOL_POST/TOOL_POST_FAIL 据 emit 返值决定 blocked_output。**bus.emit 不改**(返 last non-None 不变)。
Alternatives:
- (a) bus 加 deny 短路:语义强但改 bus 核心(所有 emit 调用方回归面大)
- 保留 ToolExecutor 硬编码(非注册式,H3 不做)
Consequences: guardrail 从 executor 硬编码 → bus 注册(可扩展其他 TOOL_PRE 消费者)。executor 据返值决策。bus 不改短路(回归面零)。
Constrains: [B]

## ADR-4: bus 不改名(MemoryEventBus 留)
Status: Accepted
Date: 2026-07-25
Context: bus 现名 MemoryEventBus(历史,memory 子系统)。泛化到全生命周期(Turn/Tool/Stop/Subagent)后名"Memory"狭义,但改名触动所有 import(engine/chat/...)+ 回归面。
Decision: 留 MemoryEventBus 名(不改)。event_bus.py docstring 更新"全生命周期(非仅 memory)"。HookRegistry 别名 defer。
Alternatives: 改名 HookRegistry(import 全改,回归面)。
Consequences: 名稍狭义但回归面零。功能泛化(Turn/Tool event 同 bus)。
Constrains: [A]

## ADR-5: Permission / PreCompact / Notification defer
Status: Accepted
Date: 2026-07-25
Context: Claude 27 含 Permission/PreCompact/Notification。AO2:guardrail block 隐含 permission(无显式 flow);无上下文压缩(PreCompact 无场景);Notification 属 TUI 侧。
Decision: Permission/PreCompact/Notification defer。仅加 6 event(ADR-1)。
Consequences: 不对标 Claude 27 全集(6 + 已有 10 = 16 event)。Permission 隐含在 guardrail deny(TOOL_PRE allow=False)。
Constrains: []
