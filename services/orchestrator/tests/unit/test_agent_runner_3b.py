"""3B:run_agent_turn 经 build_native_agent 组装器收敛(A2A 铺路)— 哨兵测。

patch ``build_model``(TestModel)免真 API/token。断言:
  - run_agent_turn 返 ``result.output``(经 agent.run,不再直调 llm.chat);
  - effective_system(arg > cfg > neutral default)进 Agent.instructions;
  - 组装器自动 prepend 纪律段(CC 5 条「Engineering Discipline」)—— 子代理与
    主 agent 同一入口,享受同一条横切;
  - tools 经 ToolBridgeCapability 注入(mock tool_executor registry 的 tool 出现在
    agent 的 tool 清单);
  - cfg_model 透传 build_native_agent(model_name=...);
  - R1 grep 自检:函数体零 memory 调用 / 零 MemoryWriterCapability 挂载。

为下次改动留哨兵:若有人退回手搓 messages + llm.chat(绕过组装器),这里立刻红。
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel

from src.agent.meta import agent_runner
from src.agent.meta.agent_runner import run_agent_turn
from src.services import _state


@pytest.fixture(autouse=True)
def _fake_model():
    """patch build_model 返 TestModel(免 ANTHROPIC_AUTH_TOKEN + 真调 API)。"""
    with patch(
        "src.harness.native_agent.build_model",
        lambda *a, **kw: TestModel(custom_output_text="SUB-OUTPUT"),
    ):
        yield


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测前清掉本次 planted 的 subagent 配置(测试隔离)。"""
    planted = []
    _orig = _state.agents.copy()

    def _plant(key, cfg):
        _state.agents[key] = cfg
        planted.append(key)

    yield _plant
    _state.agents.clear()
    _state.agents.update(_orig)


def _agent_instructions(agent) -> str:
    """组装后 Agent 传给 build_native_agent 的 instructions(私有 ``_instructions`` list)。"""
    return " ".join(getattr(agent, "_instructions", []) or [])


def _cap_instructions_text(agent) -> str:
    """capability-tier 拼接文本(纪律段居首)。"""
    return " ".join(
        c if isinstance(c, str) else getattr(c, "content", "")
        for c in agent._cap_instructions
    )


@pytest.mark.asyncio
async def test_returns_result_output_via_agent_run(_clean_state):
    """run_agent_turn 返 agent.run 的 result.output(经组装器,不再 llm.chat)。"""
    _clean_state("sub-a", {"id": "sub-a", "system_prompt": "p", "model": None})
    out = await run_agent_turn("sub-a", "hello", "sess")
    assert out == "SUB-OUTPUT"


@pytest.mark.asyncio
async def test_effective_system_arg_overrides_cfg(_clean_state):
    """system_prompt arg > cfg_system:arg 进 Agent.instructions。"""
    _clean_state("sub-b", {"id": "sub-b", "system_prompt": "CFG-PROMPT", "model": None})
    captured: dict = {}

    orig = agent_runner.build_native_agent

    def _spy(**kwargs):
        agent = orig(**kwargs)
        captured["instructions"] = _agent_instructions(agent)
        return agent

    with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
        await run_agent_turn("sub-b", "hi", "sess", system_prompt="ARG-PROMPT")
    assert captured["instructions"] == "ARG-PROMPT"


@pytest.mark.asyncio
async def test_effective_system_falls_back_to_cfg(_clean_state):
    """无 arg → cfg_system 进 instructions。"""
    _clean_state("sub-c", {"id": "sub-c", "system_prompt": "CFG-PROMPT", "model": None})
    captured: dict = {}
    orig = agent_runner.build_native_agent

    def _spy(**kwargs):
        agent = orig(**kwargs)
        captured["instructions"] = _agent_instructions(agent)
        return agent

    with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
        await run_agent_turn("sub-c", "hi", "sess")
    assert captured["instructions"] == "CFG-PROMPT"


@pytest.mark.asyncio
async def test_effective_system_neutral_default(_clean_state):
    """无 arg 无 cfg → neutral default 进 instructions。"""
    _clean_state("sub-d", {"id": "sub-d", "system_prompt": "", "model": None})
    captured: dict = {}
    orig = agent_runner.build_native_agent

    def _spy(**kwargs):
        agent = orig(**kwargs)
        captured["instructions"] = _agent_instructions(agent)
        return agent

    with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
        await run_agent_turn("sub-d", "hi", "sess")
    assert "sub-agent" in captured["instructions"].lower()


@pytest.mark.asyncio
async def test_discipline_prepended_via_assembler(_clean_state):
    """子代理经组装器 → 自动 prepend 纪律段(CC 5 条 + Engineering Discipline 标记)。

    这是 3B 的核心收益:子代理不再手搓 messages,自动享受与主 agent 同一条横切。
    """
    _clean_state("sub-e", {"id": "sub-e", "system_prompt": "p", "model": None})
    captured: dict = {}
    orig = agent_runner.build_native_agent

    def _spy(**kwargs):
        agent = orig(**kwargs)
        captured["cap_text"] = _cap_instructions_text(agent)
        return agent

    with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
        await run_agent_turn("sub-e", "hi", "sess")
    txt = captured["cap_text"]
    assert "Engineering Discipline" in txt
    assert "Tools over shell" in txt  # CC 5 条之一


@pytest.mark.asyncio
async def test_toolbridge_injects_registry_tools(_clean_state):
    """tools 经 ToolBridgeCapability 注入:ToolBridge 在 capabilities 列表里且其
    get_toolset() 含 registry 的具名 tool(证明 _state.tool_executor 被桥接到子代理)。
    """
    from src.harness.capabilities import ToolBridgeCapability

    fake_registry = type(
        "R", (), {"list_tools": lambda self: [{"name": "search_kb", "description": "search", "parameters": {"type": "object", "properties": {}}}]},
    )()
    fake_executor = type("E", (), {"registry": fake_registry})()
    saved_executor = _state.tool_executor
    _state.tool_executor = fake_executor
    _clean_state("sub-f", {"id": "sub-f", "system_prompt": "p", "model": None})
    try:
        captured: dict = {}
        orig = agent_runner.build_native_agent

        def _spy(**kwargs):
            caps = kwargs.get("capabilities") or []
            captured["caps"] = list(caps)
            return orig(**kwargs)

        with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
            await run_agent_turn("sub-f", "hi", "sess")

        bridges = [c for c in captured["caps"] if isinstance(c, ToolBridgeCapability)]
        assert len(bridges) == 1, "ToolBridgeCapability 必须挂载(子代理 tools 全覆盖)"
        # PrefixedToolset(prefix='v2')无同步 .tools 字段 → 经 async get_tools 取名(grill blocker A)
        from pydantic_ai._run_context import RunContext
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.usage import RunUsage
        ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        tool_names = set((await bridges[0].get_toolset().get_tools(ctx)).keys())
        assert "v2_search_kb" in tool_names
    finally:
        _state.tool_executor = saved_executor


@pytest.mark.asyncio
async def test_cfg_model_passed_as_model_name(_clean_state):
    """子代理配置 model → 透传 build_native_agent(model_name=cfg_model)。"""
    _clean_state("sub-g", {"id": "sub-g", "system_prompt": "p", "model": "glm-5-air"})
    captured: dict = {}
    orig = agent_runner.build_native_agent

    def _spy(**kwargs):
        captured["model_name"] = kwargs.get("model_name")
        return orig(**kwargs)

    with patch.object(agent_runner, "build_native_agent", side_effect=_spy):
        await run_agent_turn("sub-g", "hi", "sess")
    assert captured["model_name"] == "glm-5-air"


@pytest.mark.asyncio
async def test_empty_input_uses_placeholder(_clean_state):
    """空 input → 占位 '(no task input)'(防智谱 400 code 1214 空 messages)。"""
    _clean_state("sub-h", {"id": "sub-h", "system_prompt": "p", "model": None})
    out = await run_agent_turn("sub-h", "", "sess")
    # TestModel 直接返 custom_output_text,空 input 不崩即证占位生效。
    assert out == "SUB-OUTPUT"


@pytest.mark.asyncio
async def test_agent_run_failure_degrades_not_raises(_clean_state):
    """agent.run 抛异常 → 降级返错误串,不 raise(不 abort sibling agents)。"""
    _clean_state("sub-i", {"id": "sub-i", "system_prompt": "p", "model": None})

    async def _boom(*a, **kw):
        raise RuntimeError("agent boom")

    with patch("pydantic_ai.Agent.run", _boom):
        out = await run_agent_turn("sub-i", "hi", "sess")
    assert "[run_agent_turn error]" in out
    assert "agent boom" in out


def test_r1_redline_grep_zero_memory_calls():
    """R1:run_agent_turn 函数体源码零 memory 调用 + 零 MemoryWriterCapability 挂载。

    grep 实际调用形态(memory_event_bus.emit / _trigger_ingest / _trigger_kg_extraction /
    memory_service.* / MemoryWriterCapability(...) 构造)。docstring/comment 提及不计。
    """
    src = inspect.getsource(run_agent_turn)
    # 提取函数体(docstring 之后),避免 docstring 提及被误判。
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    # 去掉行注释(# ...)避免 R1 说明性注释被误判。
    body_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    body_no_comments = "\n".join(body_lines)

    forbidden = [
        "memory_event_bus.emit",
        "memory_event_bus.emit(",
        "_trigger_ingest",
        "_trigger_kg_extraction",
        "memory_service.",
        "MemoryWriterCapability(",
    ]
    hits = [tok for tok in forbidden if tok in body_no_comments]
    assert hits == [], f"R1 违规:run_agent_turn 函数体出现 memory 调用 → {hits}"


def test_r2_redline_no_main_graph_import():
    """R2:agent_runner 不 import 主路径线性图(_build_execution_graph/_node_llm/chat)。"""
    import src.agent.meta.agent_runner as m
    src_text = inspect.getsource(m)
    # import 行(非 docstring)不出现主路径禁项。
    forbidden_imports = [
        "_build_execution_graph",
        "_node_llm",
    ]
    for tok in forbidden_imports:
        # 仅检查 import 行(以 from/import 开头),非 docstring 提及。
        import_lines = [ln for ln in src_text.splitlines() if ln.lstrip().startswith(("import ", "from "))]
        assert not any(tok in ln for ln in import_lines), f"R2 违规:agent_runner import 了 {tok}"
