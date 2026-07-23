# Spec: A2A 内部 mesh
Status: Locked (2026-07-23)
ADR: docs/adr/a2a-internal-mesh.md
Iteration base: 6c69079

## Background
AO2 持久化 agent(native/main)需"可配置化管理 + 发现"。用 A2A 协议做**内部 mesh**:agent 自成 A2A 节点(serve card + 互发现 + 互消费)。pydantic-ai = 执行引擎(塞进 A2A agent 当大脑),card 从 AgentSpec 投影。详见 ADR-1~5。

## Goals (In-Scope)
- **A**:AgentSpec → AgentCard 投影 + 内部 catalog(列全 agent card)
- **B**:LocalTransport(message/send 进程内直调 build_native_agent,零网络)
- **C**:`v2_a2a_call` 工具 + ToolBridge 接入(pitfall + per-agent policy)
- **D**:observe agent_id + 嵌套 turn parent→child 追踪
- **E**:e2e native↔main 真跑(GLM,自包含)

## Out-of-Scope (defer)
HTTPTransport / 外部 A2A consume / 外部 expose(公网 card + `/.well-known`)/ consumed persistent-session 跨调用续聊 / A2A 完整 task 状态机(input-required/working/cancel)/ turn-as-graph 专项 agent 进 mesh。

## User Stories
- 作为 native agent,我在 turn 内能经 `v2_a2a_call` 发现并调用 main(main 作 peer 跑返响应)。
- 作为观察者,我能在 observe 看到嵌套 turn(native turn 内发起 main turn),带 agent_id + parent 链。
- 作为配置者,agents.yaml 驱动每个 agent 的 card(可配置化管理)。

## Acceptance
- e2e:native turn 内 `v2_a2a_call` 调 main → main 返真响应 + observe 双 turn 带 agent_id/parent + memory 各 scope。
- 全套件 pytest 不回归(735 baseline)。
- R1/R5/RK11 三红线 grep 全绿。

## Defer
见 Out-of-Scope。
