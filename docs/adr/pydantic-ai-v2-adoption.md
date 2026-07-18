# ADR: Pydantic AI 2.0 作为 agent-os-v2 自研 agent 的 in-process 执行内核

> 日期:2026-07-18。Status: Accepted。Topic: pydantic-ai-v2-adoption。
> 配套:否 pi(历史 deconstruct-spec,TS 重构停滞)、否 Eve(调研 wf_8724c38c,tool-wrapper only)。
> 依据:深读 workflow wf_461a2b24(5 维度调研 + 4 对抗验证,全 hard blocking 收敛)。

## Context

v2 有三 harness(见三 harness 全景):
- **agent-os-v2 自研**:走 `/v1/execute` 自研 graph.run,agent loop 是黑盒,无 capability 抽象,observe 推送散三套(observe/client.py 手写 / canvas/emitter.py dual-write / harness/events.py 零调用僵尸)
- **claw / claude-code**:外部 harness 实例(openclaw gateway WS / claude PTY 子进程),harness/claude.py+openclaw.py 解析其原生流

自研那种 agent loop 黑盒、能力散落、observe 三套重复。需选一个 in-process agent 执行内核收敛。候选:pi(TS,否)、Eve(TS,tool-wrapper only,否)、pydantic-ai 2.0(Python,原生)。

## Decision

**pydantic-ai 2.0(Agent + Capability + event-stream 三件套)作为 agent-os-v2 自研 agent 的 in-process 执行内核——新增一条 native 路径,不全替外部 harness 与 flow。**

1. **新增 in-process Agent 路径**:`harness/native_agent.py`,orche 内置 LLM(glm-5.2 / engine `_side_llm`)跑 pydantic-ai Agent
2. **自研能力全面 Capability 化**:ProfileCapability / MemoryCapability / GuardrailCapability / SkillCapabilityFactory
3. **ObserveCapability 只覆盖 native 路径**(`wrap_run_event_stream` 把 AgentStreamEvent → ObserveEvent)
4. **保留不动**:routes/session_store(multi-harness 外壳,ADR-4)、claude.py/openclaw.py(外部 gateway bridge)、flow.py(跨 harness DAG,事件桥机制独立)、canvas/emitter.py(前端画布通道)

## 否决的替代(对抗验证 hard blocking,wf_461a2b24)

根因硬阻塞:**v2 零 in-process pydantic-ai Agent.run 循环**(LLM 全是外部黑盒)。2.0 的 Capability/defer_loading/wrap hooks/pydantic-graph 全是 Agent.run 循环内机制,无 Agent 则无处挂载。

- ❌ **flow.py → pydantic-graph 全替**:pydantic-graph 出边靠"返回类型注解静态推断"(type-driven),v2 FlowDef 是运行时 JSON DSL 动态拓扑(data-driven),抽象正交;强行替换要写动态类型生成编译器,丢掉 graph 静态收益。且 flow.py 核心机制(emit monkey-patch + future resolve 把外部 harness 异步 turn 完成转 future)graph 不管,替换后必须保留。→ flow.py 不动
- ❌ **ObserveCapability 覆盖全部 5 种 observe 事件**:`wrap_run_event_stream`/`wrap_tool_execute` 只在 in-process Agent.run fire,v2 当前唯一事件源是 claw/claude 外部黑盒,覆盖率=0。→ ObserveCapability 只管 native 路径,外部 harness 事件解析(claude.py/openclaw.py)不动
- ❌ **execute_parallel → Agent 替换**:Agent 是 graph 节点叶子(LLM 执行),graph.run 是拓扑驱动,包含关系非替代。且 execute_parallel 在 src/api/routes/chat.py + src/graph/ 不在 harness/
- ❌ **memory→Capability 含 defer_loading 无阻塞**:断言 A(能重构 Capability)= confirmed(1:1 薄包装);但 defer_loading 依赖 Agent.run 注入 `load_capability`,无 in-process Agent 则永不激活。→ 必须先有 native Agent 路径,defer_loading 才生效

## 红线

- 不动 routes/session_store/claude.py/openclaw.py/flow.py/canvas(ADR-4/5 外壳 + 外部 harness bridge + 前端画布)
- 每阶段零回归:现有 61 harness 测试 + observe 事件流不破
- ADR-7 fire-and-forget:ObserveCapability 内 emit 异常**绝不污染** native Agent.run 主路径(try/except 包死)
- profile_registry 当前 NOT-WIRED:移植时明确接线决策,避免又造死代码

## 实施阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 深读 pydantic-ai 2.0 + 映射 v2 + 对抗验证 + 本 ADR | ✅ done(wf_461a2b24) |
| P1 | pydantic-ai 2.0 依赖(2.12.0)+ `src/harness/native_agent.py`(OpenAIChatModel 接智谱 glm-4-flash,env 覆盖;smoke 真调通) | ✅ done |
| P2 | ProfileCapability(profile.compile → get_instructions) | ✅ done |
| P3 | MemoryCapability(experience/kg tools → FunctionToolset,defer_loading) | ✅ done |
| P4 | GuardrailCapability(check → before_tool_execute + ModelRetry;补安全缺口) | ✅ done |
| P5 | ObserveCapability(native event_stream → observe,outermost,outcome/retry 分支) | ✅ done |
| P6 | SkillCapabilityFactory(SKILL.md → defer capability,对齐 load_skill) | ✅ done |
| P7 | (可选)experimental SubAgents 单-agent 内委派示水 | ⬜ defer(experimental) |
| P8 | routes 接线:native turn 走 /h/agent-os-v2(create/turn/delete/spawn + 真 ObserveEmitter + restore 分支) | ✅ done |
| P9 | graph loop ↔ pydantic-ai 桥接:`agent_turn_node`(FunctionNode 跑 Agent.run + capability 横切) | ✅ done |

## Consequences

- v2 获得第一条真正的 in-process agent 执行内核(Capability composable + defer 披露 + event stream + guardrail 接线)
- 双轨并存:native(Agent + Capability,agent-os-v2 自研归宿)+ 外部 harness(claw/claude-code,bridge 不变)+ flow(跨 harness DAG 不变)
- 引入 pydantic-ai 2.0 + pydantic-ai-harness 依赖(uv.lock 锁版本);SubAgents experimental,试水时锁版本 + HarnessExperimentalWarning
- Harness 实际只有 4 stable capability(CodeMode/FileSystem/Shell/ManagedPrompt),memory/guardrail 无 ready-made 需自写 Capability
- defer_loading / ObserveCapability / pydantic-graph 全部依赖 native Agent 路径就位后才生效——P1 是一切的前置

## 对抗验证 gap(wf_6e352996,overall = pass-with-gaps,adr_complete + spec_aligned = True)

路径/模型订正:实际 `services/orchestrator/src/harness/native_agent.py`;走智谱 **anthropic 协议**(`/api/anthropic`,AnthropicModel + glm-4.7,`ANTHROPIC_MODEL` env 覆盖,token `ANTHROPIC_AUTH_TOKEN`)—— openai 通道(coding/paas/v4)忽略 cache_control 且 model 名不同(glm-4-flash),主对话同 anthropic 通道。

关键 gap(**已修**,487 测试绿):
- ✅ P5 ObserveCapability tool_result 不读 `outcome`(denied/failed 静默当 success)+ ModelRetry 的 RetryPromptPart 误报 success → 已读 outcome 字段 + skip retry
- ✅ P8 `restore_all_sessions` 无 agent-os-v2 分支(重启不重建)→ 已加分支

defer(文档注明,非移植阻塞):
- profile/memory/skill capability 已实现但 `routes._build_native_session` 当前仅注入 ObserveCapability + GuardrailCapability;profile 注入需 `profile_registry` 接线(NOT-WIRED),memory 需 `_state.memory_service`/`knowledge_graph`,skill 需 `SkillLoader` 注入 — 属 v2 子系统接线,非 pydantic-ai 移植范围
- native session `message_history` 内存级(重启丢多轮上下文)→ 后续 observe replay 补续聊
- P1 `build_model` env 缺失时透传 openai-sdk 误导错误变量名(DX 缺口,生产 env 齐全不触发)
- native routes 测试 mock `_build_native_session`,真 glm/emitter 连接仅 `native_agent.py __main__` smoke 验证

## graph loop 聚焦编排器(2026-07-18 增补)

用户决策:**web 前端弃用**;graph loop 聚焦抽象成**多 agent 拓扑编排器**,移除与 pydantic-ai 2 重叠的部分;pydantic-ai 2 作 **agent 执行基建**。三者耦合一个分层:

| 层 | 职责 | 归属 |
|----|------|------|
| graph loop | 多 agent 拓扑(ParallelNode / FanInNode / SubgraphNode / checkpoint / resume / diamond fan-out) | **保留**——这是 graph 的真价值,pydantic-ai 单 Agent run 没有 |
| 单 agent 内部 tool 循环 | start→llm→tool→llm_synthesize + `tool_iteration`/`tool_use_history`/`needs_tool` 条件边(`tool→llm` 循环到 MAX_TOOL_ITERATIONS) | **退役** → pydantic-ai `CallToolsNode`(Agent.run 内部自动 tool 循环,重叠) |
| 单节点执行 | LLM 调用 + tool 执行 + capability 横切(observe/guardrail/profile/memory/skill) | **pydantic-ai Agent.run**(agent 基建) |

**结合点(P9,已 done)**:`src/harness/graph_agent_nodes.py` 的 `agent_turn_node()`——把「跑一轮 pydantic-ai Agent.run」封装成 graph 节点(复用现有 `FunctionNode`,不新建节点基类)。`agent_factory` 参数(默认 `build_native_agent` 接智谱 glm)让测试注入 TestModel;Agent `message_history` 存 `GraphState.context["_pydantic_messages"]`(与老的 `state.messages: list[dict]` 类型隔离,后者供退役的 tool 循环 / memory hooks)。

验证(零破坏,纯新增):7 单测绿(agent_turn_node 跑通 + output/messages 回写 + 续聊 history 透传 + 两节点 graph.run 编排 + ParallelNode+FanIn 多 agent 拓扑);全 orchestrator **862 passed** 零回归(7 failed + 8 errors 皆预存环境:memory graph endpoint / phase7 VectorStore / canvas e2e)。

**后续阶段(本轮不做,破坏性,等桥接稳定再退役)**:
- 退役 `chat.py` `_node_llm`/`_node_tool`/`_node_llm_synthesize` + `tool_iteration`/`tool_use_history`/`needs_tool` 条件边 → 单 agent graph 退化为一个 `agent_turn_node`(Agent 内部跑完 tool 循环);`_node_tool` 的 observe 副作用归 ObserveCapability(已建)
- web 前端弃用:`/v1/execute` 给 web 的 SSE 通道隔离退役(TUI/observe 路径不受影响;graph loop 本体保留作 native 编排内核)
- 多 agent 入口(`_build_parallel_graph` / `_build_multi_agent_graph`)每分支 handler 换 `agent_turn_node`(并行 + capability)

### 多节点 workflow 对抗验证(2026-07-18,7 agent:survey×4 + design + verify×2)

针对「graph loop 聚焦编排器」后续阶段跑多节点 workflow 勘察→设计→对抗验证。两 verify agent 一致裁决 **safe-with-gaps**:方向正确,但发现真实语义鸿沟,**主路径退役不可盲目做**。

**本轮已安全落地**:
- ✅ **web 弃用**:`apps/web/DEPRECATED.md`(前端标记)+ 删 gateway SSE 代理 `services/gateway/src/routes/execute.py`(web 专用,web 弃用后零消费者;`main.py` + `conftest.py` 同步清理;gateway test_routes 6 passed 零回归)
- ✅ orchestrator `/execute` 端点保留(graph 执行引擎,native turn 复用,非「web 前端」范围)
- ✅ gateway `orchestrate`/`chat` 代理暂留(待 gateway 整体弃用决策)

**must_defer(语义鸿沟,workflow 对抗验证发现)**:
| 阶段 | 阻塞原因(语义鸿沟) |
|------|---------------------|
| P3 退役 `_node_start` | 与 `_node_llm`/`_node_tool` 退役(P8)+ `test_multi_turn_tool_loop` 重写(P9)耦合,留同 PR;`on_node_complete` 有 start 分支(output 有 `or "started"` 兜底) |
| **P5 MemoryWriterCapability** | pydantic-ai `after_run` 在 Agent.run 结束**一次性**触发 → 丢老 `_node_tool` 每轮 tool_result 逐条 TURN_END 沉淀(中间轮记忆,静默丢功能)。须改 `wrap_run_event_stream` 在 `FunctionToolResultEvent` **per-result** emit |
| P6 CanvasCapability | `_node_tool` canvas 双发(ToolCall/ToolResult)无 capability 承接;ObserveCapability 只推 observe-service(8002)不推 canvas(两条独立通道) |
| **P7 PitFailCapability** | `tool_executor.execute` 返 `{status:'error'}` 是**返回值非 raise**;pydantic-ai `on_tool_execute_error` 只在 raise 触发。tool wrapper 须显式翻译,非 hook 自动接管 |
| **P8 `/execute` 主路径退役** | 依赖 P5/P6/P7 全绿;必破 `test_multi_turn_tool_loop`(6)+ `test_function_calling::TestNodeToolWithNativeToolUse`;`agent_turn_node` 已存在但 handler 当前零副作用承接(P5/P6/P7 是真前置) |
| **R2 cache_control** | `agent_turn_node → build_native_agent` 用裸 AnthropicModel,ContextCompiler/static_count/apply_cache_control 完全旁路。AnthropicModelSettings 原生支持 cache_control 三层粒度(已核实)但默认粒度 ≠ 自研 static_count 精确控制,智谱 /api/anthropic 命中率可能变。P8 前须实测迁移前后 cache hit/token;需精确控制则写 `wrap_model_request` capability 插 CachePoint |

**执行序(verify agent 建议)**:web 弃用(✅)→ 修 P5 memory 时序 + P7 tool-error 语义后建 P5/P6/P7(各独立单测)+ 实测 R2 → P8 主路径退役 + P9/P10 测试重写同 PR → P11 synthesizer 换 agent_turn_node(可选;若分支不需 tool/observe 则不换,ponytail)。

**关键已核实**:pydantic_ai 2.12.0 `AbstractCapability` 暴露 `after_run`/`before_run`/`wrap_run`/`wrap_run_event_stream`/`wrap_tool_execute`/`on_tool_execute_error`/`wrap_model_request` 全套钩子(P5/P6/P7 技术可行,阻塞在语义非 API)。

### P5 MemoryWriterCapability 鸿沟已解 + 落地(2026-07-18)

workflow must_defer 的 P5 鸿沟(`after_run` 丢中间轮 tool_result 沉淀)**已解**:改用
`wrap_run_event_stream`(非 `after_run`)在 `FunctionToolResultEvent` **per-result** emit ——
N 轮 tool = N 次 TURN_END(tool_result_item)+ INGEST 沉淀,不丢中间轮;stream 耗尽触发用户轮
四件套(TURN_END working + INGEST + KG + PRE_COMPRESS)。`ctx.prompt` / `ctx.messages` 提供
user_prompt / history。

- `src/harness/capabilities/memory_writer_capability.py`:写侧 capability(`defer_loading=False`,
  不暴露 tool → 不破 R1;`position="inner"` 让 observe 包外做完整 tick 闭环)。env gate
  (bus/kg None → no-op)+ ADR-7(全 try/except + fire-and-forget,不污染 Agent.run)
- 接入 `_build_native_session`(P5 通电):native `/h/agent-os-v2` turn 注入 MemoryWriter
  (`_state.memory_event_bus` + `_state.knowledge_graph`)
- 7 单测绿(per-result 独立沉淀 / 用户轮四件套 / env gate / emit 失败不破主路径 / retry +
  failed outcome 不误沉淀)

剩余 must_defer 不变:P6 Canvas(canvas 双发无 capability 承接)/ P7 PitFail
(`{status:'error'}` 返回值非 raise,`on_tool_execute_error` 不触发)/ P8 主路径退役(依赖
P5/P6/P7 + test_multi_turn_tool_loop 重写)/ R2 cache_control 实测。

## 后续可选(非本 ADR 范围)

- flow.py 拓扑层未来若评估用 pydantic-graph,需先解 data-driven DSL → type-driven graph 编译器问题(本 ADR 不做)
- claw/claude-code turn 包成 FunctionToolset 让 native Agent delegate(voice P5 external adapter)——评估后定

> 关联:[[harness-bridge-orchestrator]](harness-bridge-orchestrator.md)、[[session-mgmt]](session-mgmt.md)
