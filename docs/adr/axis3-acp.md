# ADR: axis3-acp
Date: 2026-07-25
Status: Active(Design ADR,阶段 3 条件启动前冻结判据)

## ADR-1: 核心判据 = 驱动 vs 内容声明式
Status: Accepted
Date: 2026-07-25
Context: 三次修正后明确:agent 间交互分两种性质——硬编码驱动流程(deterministic 内核调度)vs 内容声明式(declarative agent 间通信协议)。之前混为一谈(fan-out 算 mesh/ACP、编排投影到 ACP)致概念混乱。
Decision: 判据二分。**硬编码驱动流程**(workflow fan-out/spawn/fork/async/cancel)= 确定性内核调度,**不走 ACP**(直接 build_native_agent+run)。**内容声明式**(A2A consume peer/agent 间声明通信)= 内容协议,**走 ACP**(A2A over ACP)。
Alternatives:
- 全走 ACP(fan-out 也走管道):过度,驱动流程是内核逻辑非内容,无需管道开销
- 全不走 ACP(A2A 也进程内 str):a2a LocalTransport 太薄,撑不起流式 handoff
Consequences: fan-out/spawn 保留现有内核机制(workflow_engine R1 不动)。A2A consume 升级走 ACP。判据清晰,不再"两套并存"对立。
Constrains: []

## ADR-2: workflow fan-out/spawn 不走 ACP(硬编码驱动)
Status: Accepted
Date: 2026-07-25
Context: workflow_run/workflow_loop fan-out N sub-agent 是 workflow_engine.py(R1 红线)的确定性调度,是驱动流程非内容。spawn 单 agent 一次性同理。
Decision: fan-out/spawn **不走 ACP**,直接 build_native_agent+run(现有机制)。ACP 不介入驱动。workflow_engine.py/flow.py(R1)不动。
Alternatives: fan-out 也走 ACP(把 N sub-agent 当 mesh)——但 fan-out 是内核调度逻辑(谁先跑/并行/聚合),非内容通信,无需管道。
Consequences: 保留 workflow 红线(R1)。fan-out 的状态(sub-agent 结束)fire SUBAGENT_STOP(axis2 已做)= 投影到 hook,非走 ACP。
Constrains: []

## ADR-3: A2A consume 走 ACP(A2A over ACP)
Status: Accepted
Date: 2026-07-25
Context: a2a_call consume peer 是 agent 间声明式内容通信(调用/返回/委托),当前 LocalTransport 进程内 str 一次性太薄(无 parts/streaming/session)。A2A 是协议,与 ACP 管道不是并列关系。
Decision: **A2A ⊂ ACP**(A2A over ACP)。A2A 协议语义不变,传输从 LocalTransport 升级为 ACP 承载(parts/streaming/多轮 session)。ACP 是 A2A 的传输载体,非并列两套。
Alternatives:
- A2A 独立于 ACP(并列):概念割裂,A2A 传输散落
- 维持 LocalTransport(str 一次性):撑不起流式 handoff conversational
Consequences: A2A 协议层(语义)稳定,传输层升级。consumed peer scope(ADR-4 peer 全套能力)不变。
Constrains: [阶段3]

## ADR-4: ACP 取代内容传输(非驱动方法)
Status: Accepted
Date: 2026-07-25
Context: ACP 的定位是**内容传输管道**,不是驱动方法。之前"ACP 取代 ws 方法驱动"表述误导(驱动是硬编码内核,ACP 不取代)。
Decision: ACP 取代**内容传输**——a2a LocalTransport(str 一次性)+ 三方 harness 直连(openclaw ws :18789 / claude-code stream-json)在 AO2 侧翻译到 ACP。**不取代驱动方法**(workflow_engine/spawn/fork 硬编码不动)。
Alternatives: ACP 取代驱动(把调度也进管道)——驱动是内核逻辑,进管道是过度。
Consequences: AO2 代码只见 ACP transport(agent 间内容);驱动仍内核。三方 adapter(defer)翻译协议帧,harness 内部不动(claw ws 不动)。
Constrains: [阶段3]

## ADR-5: 投影 = 驱动状态 → hook event(观测,非驱动走 ACP)
Status: Accepted
Date: 2026-07-25
Context: "硬编码方法投影到内容"原表述过宽,易误解为驱动走 ACP。实际:驱动流程不走 ACP,但其**状态变化**可投影到观测(hook event)。
Decision: 投影 = 驱动流程的状态变化 fire hook event(进 MemoryEventBus/observe),**非驱动本身走 ACP**。axis2 已实现:workflow/a2a consumed 结束 fire `SUBAGENT_STOP`(驱动状态 → hook 观测)。
Alternatives: 驱动走 ACP(把状态也进管道)——hook event 更轻量(进程内 fire-and-forget),够观测用,无需 ACP 重管道。
Consequences: axis2 的 SUBAGENT_STOP(及其他生命周期 hook)= 驱动投影的实现,**已做,保留**。驱动不走 ACP,投影走 hook。
Constrains: []

## ADR-6: hook 生命周期(MemoryEventBus)与 ACP 内容管道正交,不统一
Status: Accepted
Date: 2026-07-25
Context: axis2 的 hook event(TURN/TOOL/STOP/SUBMIT/SUBAGENT_STOP 经 MemoryEventBus)与 axis-3 ACP 内容管道都是"内容"相关,是否统一?
Decision: **正交,不统一**。hook event = 单向 fire-and-forget 生命周期观测(进程内 MemoryEventBus,消费者 memory/observe/neural/guardrail);ACP = 双向 agent 间内容通信(parts/streaming,承载 A2A)。两者不同通道/消费者/语义。axis1/axis2 已改代码**全部保留不调整**。
Alternatives: 统一(hook 也进 ACP)——hook 进程内轻量够,ACP 是 agent 间重管道,统一是过度(除非未来 hook 要跨 harness)。
Consequences: axis2 hook 保留(MemoryEventBus)。ACP 新增(agent 间)。阶段 1/2 成果不回改。
Constrains: []

## ADR-7: 三方 adapter / session 注册表 defer(条件启动)
Status: Accepted
Date: 2026-07-25
Context: ACP 全套(三方 adapter + session 注册表)依赖 a2a 瓶颈验证。当前 LocalTransport 可能够 MVP。
Decision: 阶段 3 P0 先验证 a2a 够不够(真机测 workflow fan-out + handoff)。痛点成立才建 ACP + A2A over ACP。三方 adapter / /v1 注册表 / session 管理层 defer(看痛点)。
Consequences: 不预设建 ACP(避免 over-engineering)。先验证后启动。
Constrains: []
