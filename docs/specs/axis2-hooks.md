# Spec: axis2-hooks
Date: 2026-07-25
Status: Draft → 待 go Locked
Base: 1867b95

## 1. Problem
Tool 层 hook 硬编码(ToolExecutor guardrail.check 直调 executor.py:74/106),非 bus 注册;Tool/Stop/Submit/Subagent event 缺失;TURN_START reserved 未 fire。流式编排收口(SubagentStop)、tool 失败可观测(PostToolUseFailure)缺。

## 2. Solution
H1 扩 6 EventType + Context + MemoryHook 方法(ADR-1);H2 5 fire 点(ADR-2);H3 guardrail 注册式 + ToolExecutor 据返值(bus 不改 ADR-3)。bus 不改名(ADR-4)。Permission/PreCompact/Notification defer(ADR-5)。

## 3. Out-of-Scope(defer)
- Permission/PreCompact/Notification event(无场景,ADR-5)
- bus 改名 HookRegistry(回归面,ADR-4)
- 8 预存 fail 隔离(继承 axis1 defer)

## 4. User Stories
- 作为开发者,我能注册 TOOL_PRE 消费者(如审计/限流),不硬编码进 ToolExecutor
- 作为开发者,tool 失败 fire TOOL_POST_FAIL,observe 可观测(当前 except 无 fire)
- 作为开发者,workflow/a2a consumed agent 结束 fire SUBAGENT_STOP,流式编排可收口

## 5. Implementation Decisions
- [ADR-1] H1 扩 6 EventType + Context + MemoryHook 方法
- [ADR-2] H2 5 fire 点
- [ADR-3] H3 guardrail 注册式 + ToolExecutor 据返值(bus 不改)
- [ADR-4] bus 不改名
- [ADR-5] Permission/PreCompact/Notification defer

## 6. Testing Decisions(seams)
- qa_available=false → verify 走 general_test(grep + pytest)+ skeptic
- 回归基线:pytest 全套件 vs base 1867b95(1215 passed + 8 fail 预存,继承 axis1)
- skeptic:bus 机制不变(register/emit/priority/degradation)+ guardrail 行为等价(注册式 vs 硬编码)+ 红线(R1 workflow_engine/flow.py、R5 observe memory_event_bus 不改语义、RK11 注册名)

## 7. Acceptance(节点映射)
- A.T1(H1):event_bus 6 EventType + Context + MemoryHook 方法 + 单测 fire/聚合
- B.T1(H3+H2 Tool):ToolExecutor fire TOOL_PRE/POST/POST_FAIL + guardrail 注册式 + 据返值决策 + 删硬编码
- B.T2(H2 Turn):trigger_turn SUBMIT + agent.run TURN_START/STOP
- B.T3(H2 Subagent):workflow/a2a consumed SUBAGENT_STOP

## 8. Open Issues
(无,grill 收敛)

## 9. Defer 预判
- Permission/PreCompact/Notification event
- bus 改名 HookRegistry + 别名
- TOOL_PRE 多消费者扩展(限流/审计,当前仅 guardrail)
