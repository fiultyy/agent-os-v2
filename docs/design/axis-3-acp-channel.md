# 轴 3:ACP 内容管道 — 内容声明式走 ACP,硬编码驱动不走

Date: 2026-07-25(三次修正:判据 = 驱动 vs 内容声明式)
Status: Design(待 plan,阶段 3 条件启动)
依赖:无(独立,与 axis1/axis2 正交)

## 核心判据:驱动 vs 内容声明式

| 性质 | 例子 | 走 ACP? |
|---|---|---|
| **硬编码驱动流程**(deterministic) | workflow fan-out / spawn / fork / async / cancel | **不走 ACP**(内核直接 build_native_agent+run) |
| **内容声明式**(declarative) | A2A consume peer / agent 间声明通信 | **走 ACP**(A2A over ACP) |

**判据**:是确定性驱动流程(内核调度),还是声明式内容通信(agent 间协议)。
- 驱动流程 → 硬编码在内核(`workflow_engine.py` R1 不动,spawn/fork 原语),直接调度,**不经 ACP**
- 内容声明式 → agent 间声明通信(A2A 协议),**经 ACP 管道**(parts/streaming/session)

## 三层正交

```
┌─ 硬编码驱动层(内核,不走 ACP)────────────────────────┐
│ workflow_engine.py(R1 红线)/ spawn / fork / async    │
│ = 确定性驱动流程,直接 build_native_agent+run         │
│ (fan-out N sub-agent 是内核调度,非内容)              │
└────────────────────────┬───────────────────────────────┘
                         │ 驱动状态 → hook event(投影,观测,非走 ACP)
                         ▼
┌─ A2A 协议层(内容声明式,over ACP)─────────────────────┐
│ A2A = agent 间声明通信(调用/返回/委托/card 语义)    │
│ A2A over ACP(ACP 承载 A2A,非并列)                  │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌─ ACP 内容管道层(传输)────────────────────────────────┐
│ ACP = parts/streaming/双向/session                    │
│ 承载内容声明式(A2A)+ 三方 harness adapter 翻译       │
│ 取代散落(LocalTransport str 一次性 / ws 方法驱动)   │
└───────────────────────────────────────────────────────┘
```

## 纠正(历史错误二分)

| 曾设计(错) | 修正 |
|---|---|
| workflow fan-out 算 mesh/ACP | fan-out 是**硬编码驱动流程**,**不走 ACP**(内核直接调度) |
| 编排投影到 ACP | 驱动**不走 ACP**;投影 = 驱动**状态 → hook event**(观测,非驱动走管道) |
| A2A 与 ACP 并列 | A2A ⊂ ACP(**A2A over ACP**,内容协议封装进管道) |
| ACP vs spawn 两套并存 | 按性质**分流**:驱动走内核,内容走管道(非对立并存) |
| ACP 取代 ws 方法驱动 | ACP 取代**内容传输**(ws/LocalTransport 投影到 ACP),非取代驱动方法 |

## 0. 为什么 fan-out 不走 ACP(关键)

workflow fan-out(workflow_run/workflow_loop)是**硬编码驱动流程**:
- `workflow_engine.py`(R1 红线)确定性调度 N 个 sub-agent(build_native_agent + run + gather)
- 这是**内核调度逻辑**(谁先跑/并行/聚合),不是**内容通信**
- 直接进程内调度即可,无需 ACP 管道(parts/streaming 是给内容声明式用的)
- **不走 ACP** = 保留现有 workflow 机制(R1 不动)

A2A consume peer(a2a_call)是**内容声明式**:
- agent 间声明"我要调用 peer X,传 message,收 response"
- 这是**内容通信**(语义协议),需管道承载(parts/streaming/多轮)
- **走 ACP**(A2A over ACP)

## 1. 现状
- **a2a LocalTransport**(`a2a/transport.py`):进程内 `send(str)→agent.run→str`,一次性纯文本 = A2A 的**简陋实现**(协议语义在,传输太薄:无 parts/streaming/session)
- **编排硬编码**:`workflow_engine.py`(R1)/ spawn / fork / `routes.py`(不动)
- **三方 harness 协议**:claude-code stream-json / openclaw ws :18789 / native pydantic-ai 进程内

## 2. 目标
1. **建 ACP 内容管道**(`src/acp/`):parts/streaming/双向/session
2. **A2A over ACP**:LocalTransport 升级为 ACP 承载(A2A 协议语义不变,传输走 ACP)
3. **驱动不动**:workflow_engine/spawn/fork 硬编码(R1 不变),状态投影到 hook event(axis2 已做 SUBAGENT_STOP)
4. **三方 adapter**(defer):openclaw ws / claude-code stream-json → ACP 翻译(claw ws 不动)

## 3. 详细设计

### 3.1 硬编码驱动层(不走 ACP,不动)
`workflow_engine.py`(R1)/ spawn / fork / async / cancel — 确定性内核调度,直接 build_native_agent+run。**ACP 不介入驱动**。fan-out 的 N sub-agent 调度是内核逻辑,非内容。

### 3.2 A2A 协议层(内容声明式,over ACP)
A2A = agent 间声明通信语义(调用/返回/委托/AgentCard 投影)。**A2A over ACP**:协议语义不变,传输从 LocalTransport 升级为 ACP 承载(白得 parts/streaming/多轮 session)。

### 3.3 ACP 内容管道层 `src/acp/`
- `protocol.py`:JSON-RPC stdio(loopback 进程内 / 跨进程 stdio defer)+ parts(Text/Data/Tool/File)+ session/turn lifecycle
- `transport.py`:ACPTransport(send parts → streaming parts 输出,替代 LocalTransport 的 str 一次性)
- consumed agent peer scope 不变(ADR-4 peer 全套能力)

### 3.4 投影(驱动状态 → hook,非走 ACP)
驱动流程(workflow/spawn)的**状态变化** fire hook event(axis2 已实现):
- `SUBAGENT_STOP`(consumed agent 结束,axis2 加)= 驱动状态投影到 event bus(观测)
- 这是"投影"(状态 → hook),**非驱动走 ACP**(驱动仍内核调度)
- observe 从 hook event 推(经 MemoryEventBus,axis2 通道)

## 4. 与 axis2 hook 正交(不统一)
- **hook event**(MemoryEventBus)= 单向 fire-and-forget 生命周期观测(TURN/TOOL/STOP/SUBMIT/SUBAGENT_STOP),进程内,消费者 memory/observe/neural/guardrail
- **ACP 管道** = 双向 agent 间内容通信(parts/streaming),承载 A2A
- 两者正交:hook 是观测(生命周期标记),ACP 是通信(内容流转)。**不统一**(hook 进 MemoryEventBus 够,ACP 是 agent 间;除非未来 hook 要跨 harness 才进 ACP)。

## 5. 条件启动(阶段 3 P0 先验证)
**先验证 a2a LocalTransport 够不够**,痛点成立才建 ACP:
1. a2a str 一次性是否真卡流式编排?(A2A consume peer 当前用 LocalTransport,实测)
2. handoff conversational(多轮上下文交接)是否真需 parts/streaming?
3. turn 404 是否真痛到建 session 注册表?(auto-create 兜底够否)

痛点成立 → 建 ACP + A2A over ACP。够用 → defer ACP,维持 LocalTransport。

**P0 验证结果(2026-07-25):a2a 够 → ACP defer**。
- a2a LocalTransport:`send(str)→Message(TextPart)`,进程内同步,fresh turn(peer scope ADR-4)
- 唯一消费点:`a2a_call_handler`(v2_a2a_call 工具,模型 consume peer 一次性问答,返 str)
- 流式编排(fork/async/fan-out)走 `build_native_agent` 硬编码驱动(ADR-2),**不走 a2a**
- grep 确认无 streaming/多轮/parts 真实需求(AgentCard `streaming=False`,transport `fresh turn MVP`)
- 当前 AO2 agent 间交互皆一次性 consume;流式 handoff(`a2a-streaming-orchestration-design` 设计)未实施

→ 痛点**不成立**,**ACP defer**(ADR-7 条件启动验证通过)。未来"流式 handoff conversational"(peer 边想边吐 + 多轮上下文)成真实需求才启动 ACP + A2A over ACP。

## 6. defer
- 三方 adapter(openclaw ws↔ACP / claude-code stream-json↔ACP):先做好 AO2 自身
- /v1/* 注册表接入:先做好 AO2 session 管理层
- ACP server 模式(暴露给 Zed):另迭代
- ACP 跨进程 stdio:loopback 进程内先(MVP 够)
- streaming backpressure:启动 ACP 再测

## 7. 回退记录(三次修正)

**一次设计**:ACP 全套(protocol + native adapter + 三方 adapter + streaming + agent 间 transport)。

**二次修正(grill v2)**:session 不统一改严格管理层 / ACP vs spawn 两套并存 / ws adapter。

**三次修正(本轮,判据=驱动 vs 内容声明式)**:
- 读代码 + 用户精炼点穿:fan-out 不走 ACP 是因为**硬编码驱动流程非内容声明式**
- 纠正"编排投影到 ACP"(过宽)→ 驱动**不走 ACP**,投影 = 驱动**状态 → hook event**(观测)
- 纠正"A2A 与 ACP 并列" → A2A ⊂ ACP(A2A over ACP)
- 核心判据:**驱动(硬编码流程,内核)vs 内容声明式(A2A,管道)**,按性质分流非对立

**保留洞察**:session 严格管理层(条件)/ A2A over ACP / 投影 = hook event(axis2 已做)。
