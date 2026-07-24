# ADR: cd-cleanup
Date: 2026-07-24
Status: Active
Iteration base: ef28c2d

## ADR-C1: _state 装配根治(模块级 → bootstrap 可重置)
Status: Accepted
Context: engine.py:62-107 模块级装配 `_state`(llm_client/knowledge_graph/memory_service/.../agent_registry/profile_registry),import engine 即触发。全套件跑时 `_state` 单例跨测试共享:前面测试 load 真 agents.yaml(help)→ `_state.agent_registry` 含 help → 后续 `test_harness_routes_session._build_native_session` 系列拿污染 registry(期望 native/空)→ 6 unit 确定性失败。memory feedback-workflow-verify-blindspot 实锤(engine.py 模块级装配是元凶)。dev 阶段未发布,用户选根治(非 conftest 补丁)。
Decision: 把 engine.py 模块级装配(line 62-107)包成 `bootstrap()` 函数(import engine 不触发装配)。FastAPI app lifespan 启动时调 `bootstrap()`(生产启动装配,行为等价)。`_state` 加 `reset()` 方法(清所有字段 → None)。conftest.py 加 fixture(涉及 _state 的测试前后 `_state.reset()` + 按需 `bootstrap(test_config)`),实现真隔离。可选 lazy property 作字段级 fallback(subagent 选最简根治形态)。
Consequences: import engine 零副作用(不再 import 时装配)→ 测试隔离 naturally。生产靠 lifespan 装配(必须验证 orche 真启动健康)。动生产代码(engine.py + src/services.py _state 定义 + conftest),风险:启动顺序/字段依赖(knowledge_graph 先于 memory_service)须保。全套件 6 unit 失败清零(仅剩 2 e2e GLM flake)。
Constrains: [C1.T1, C1.T2]

## ADR-C2: AgentRegistry 公开方法 + list_ao2_agents 改用
Status: Accepted
Context: list_ao2_agents 端点(routes.py:896)直读 `registry._default_id` + `registry._agents`(私有属性)。功能正确但不规范,且 C1 重构 _state 后更应走公开接口。
Decision: `AgentRegistry` 加公开方法 `default_id() -> str|None` + `iter_agents() -> Iterator[tuple[str, AgentSpec]]`(或 `agents_view()`)。list_ao2_agents 端点改用公开方法(不读 `_default_id`/`_agents`)。
Constrains: [C2.T1]

## ADR-D: help 挂 ao2-architecture(重度任务向)
Status: Accepted
Context: 系统是定制 TUI + bridge cc/claw 的重度任务编排平台,help agent 不应只是浅层新手向导,用户问架构细节时应能直接 load ao2-architecture 答。现状 help `skills:[ao2-help]`,ao2-help 文本指路 ao2-architecture 但 help 不能 load_capability(不在白名单)。
Decision: help `skills: [ao2-help, ao2-architecture]`。help 能 load 两个 skill:a2-help(业务配置)+ ao2-architecture(架构详解)。ao2-architecture 不再是"被指路但不可 load",help 直接答架构问题。
Constrains: [D.T1]
