"""P1: 多轮 tool_use loop 回归测试。

覆盖三层场景:
1. **多轮** — mock LLM 第1轮 tool_use(A) → _node_tool → 第2轮 tool_use(B) →
   _node_tool → 第3轮无 tool_use → llm_synthesize。验证 tool→llm 循环回边、
   tool_result 回注、tool_iteration 计数、最终综合。
2. **max_iterations** — mock LLM 永远 tool_use → 超限(MAX_TOOL_ITERATIONS)强制
   llm_synthesize,不死循环。
3. **单轮回归** — 无工具(needs_tool False)→ 直接 synthesize;一轮工具 →
   tool→llm(无新 tool_use)→synthesize。

测试策略:直接组装 ``_build_execution_graph()`` 生产的图,把 ``_state`` 的最小
依赖(llm_client / tool_executor / agents / context_compiler / memory_event_bus)
换成轻量 stub/mock,``graph.run`` 跑完整个 loop。这样验证的是真实图边 + 真实
节点处理器,而不是手工编排的调用序列。
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# pysqlite3 替换坏掉的 miniconda3 sqlite3(src.memory 链式 import 需要)。
try:
    import pysqlite3

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

# .venv 可能缺 httpx,注入 stub(llm_client 仅在 import 时引用类型)。
try:
    import httpx  # noqa: F401
except ImportError:
    _fake_httpx = types.ModuleType("httpx")
    _fake_httpx.AsyncClient = object
    _fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    _fake_httpx.RequestError = type("RequestError", (Exception,), {})
    sys.modules["httpx"] = _fake_httpx

import pytest

from src.api.routes import chat as chat_mod
from src.graph import GraphState
from src.services import _state


# ── stub 装配 ────────────────────────────────────────────────────────


class _CompiledCtx:
    """CompiledContext 替身:直接把传入 messages 透传。"""

    def __init__(self, messages, static_count=None):
        self.messages = messages
        self.static_count = static_count


class _StubCompiler:
    """绕过真实记忆召回的 ContextCompiler — messages 原样返回,static_count=None。"""

    async def compile(self, *, system_prompt, conversation, agent_id, session_id,
                      cache_breakpoint=True):
        # 把 system_prompt 作为第一条 system message 注入,与真实编译器形状一致。
        msgs = [{"role": "system", "content": system_prompt}] + list(conversation)
        return _CompiledCtx(msgs, static_count=None)


class _ScriptedLLM:
    """按脚本依次返回响应并设置 last_tool_use 的 LLMClient 替身。

    ``scripts`` 是一个 list,每个元素是 ``{"text": str, "tool_use": dict|None}``。
    每次调用 ``chat`` 弹出下一个脚本项。``last_tool_use`` per-call reset 语义
    由本类直接维护(无 tool_use 时置 None)。
    """

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._cursor = 0
        self.last_tool_use = None
        # 记录每次调用传入的 messages,供断言 tool_result 回注结构。
        self.calls = []

    async def chat(self, messages, model=None, static_count=None, tools=None, **kw):
        self.calls.append(list(messages))
        if self._cursor >= len(self._scripts):
            raise AssertionError(
                f"_ScriptedLLM 脚本耗尽:第 {self._cursor + 1} 次调用无对应脚本"
            )
        item = self._scripts[self._cursor]
        self._cursor += 1
        tu = item.get("tool_use")
        # per-call reset:无 tool_use 必须置 None(还原 LLMClient 语义)。
        self.last_tool_use = dict(tu) if tu else None
        return item.get("text", "")


class _StubToolExecutor:
    """ToolExecutor 替身:每个工具返回固定 output,status=success。

    ``registry = None`` 使 ``_build_native_tools`` 返回 [](无原生 schema),
    这样 ``_node_llm`` 调 LLM 时不传 tools — 由 _ScriptedLLM 的脚本决定
    是否产出 tool_use(不受真实 tool schema 影响)。
    """

    def __init__(self, outputs):
        # outputs: {tool_name: result_anything}
        self._outputs = outputs
        self.registry = None

    async def execute(self, name, args):
        out = self._outputs.get(name, f"<no stub for {name}>")
        return {"status": "success", "output": out}


class _NoopEventBus:
    """MemoryEventBus 替身:emit 直接返回 None(无 hook)。"""

    async def emit(self, event_type, ctx):
        return None


@pytest.fixture
def assembled_state():
    """备份并还原 _state 的关键字段,让每个测试装配自己的最小依赖。"""
    saved = {
        k: getattr(_state, k, None)
        for k in (
            "llm_client", "tool_executor", "agents", "context_compiler",
            "memory_event_bus", "knowledge_graph", "task_consolidator",
            "write_queue",
        )
    }
    _state.agents = {"cli": {"model": "m", "system_prompt": "You are helpful."}}
    _state.context_compiler = _StubCompiler()
    _state.memory_event_bus = _NoopEventBus()
    _state.knowledge_graph = None
    _state.task_consolidator = None
    _state.write_queue = None
    yield
    for k, v in saved.items():
        setattr(_state, k, v)


def _run_graph(state):
    """构造执行图并运行(图边由 _build_execution_graph 定义)。"""
    g = chat_mod._build_execution_graph()
    return g


# ── 1. 多轮 tool_use loop ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_turn_two_tools_then_synthesize(assembled_state) -> None:
    """第1轮 tool_use(A) → tool → 第2轮 tool_use(B) → tool → 第3轮无 tool → synthesize。

    验证:
    - tool→llm 循环回边(图不再 tool→llm_synthesize 单轮终止)。
    - tool_iteration 在两轮工具后 == 2。
    - tool_use_history 累积两条 {tool_use, tool_result}。
    - 最终 synthesize 读全部历史生成回复。
    - llm 节点被进入 3 次(首轮 + 2 次 tool 回边后)。
    """
    llm = _ScriptedLLM([
        {"text": "call A", "tool_use": {"name": "tool_a", "input": {"x": 1}}},
        {"text": "call B", "tool_use": {"name": "tool_b", "input": {"y": 2}}},
        {"text": "done deciding", "tool_use": None},  # 第3轮无工具 → 路由 synthesize
        {"text": "FINAL-SYNTH", "tool_use": None},     # synthesize 节点的 LLM 调用
    ])
    _state.llm_client = llm
    _state.tool_executor = _StubToolExecutor({
        "tool_a": "result-A",
        "tool_b": "result-B",
    })

    g = _run_graph(GraphState(input="do A then B", agent_id="cli", session_id="s1"))
    final = await g.run(GraphState(input="do A then B", agent_id="cli", session_id="s1"))

    # 两轮工具执行后 tool_iteration == 2(每个 _node_tool 末尾 +1)。
    assert final.tool_iteration == 2
    # 累积两条历史。
    assert len(final.tool_use_history) == 2
    assert final.tool_use_history[0]["tool_use"]["name"] == "tool_a"
    assert final.tool_use_history[0]["tool_result"] == "result-A"
    assert final.tool_use_history[1]["tool_use"]["name"] == "tool_b"
    assert final.tool_use_history[1]["tool_result"] == "result-B"
    # 两条 tool_results。
    assert [r["tool"] for r in final.tool_results] == ["tool_a", "tool_b"]
    # 最终在 synthesize 节点收尾,output 是 synthesize 的回复。
    assert final.current_node == "llm_synthesize"
    assert final.output == "FINAL-SYNTH"
    # LLM 被调用 4 次(3 次工具决策 + 1 次 synthesize)。
    assert len(llm.calls) == 4
    # 无错误。
    assert final.errors == []


@pytest.mark.asyncio
async def test_tool_result_history_injected_into_messages(assembled_state) -> None:
    """多轮 loop 的第2轮 LLM 调用,messages 必须含上一轮的 tool_use/tool_result
    content block 序列(anthropic 回注)。"""
    llm = _ScriptedLLM([
        {"text": "call", "tool_use": {"name": "tool_a", "input": {"x": 1}}},
        {"text": "final decision", "tool_use": None},  # 第2轮无工具 → synthesize
        {"text": "SYNTH", "tool_use": None},            # synthesize 节点
    ])
    _state.llm_client = llm
    _state.tool_executor = _StubToolExecutor({"tool_a": "RR"})

    g = _run_graph(GraphState(input="q", agent_id="cli", session_id="s"))
    await g.run(GraphState(input="q", agent_id="cli", session_id="s"))

    # 第2次 LLM 调用(下标1)应包含 tool_use / tool_result block。
    second_call_msgs = llm.calls[1]
    flat = []
    for m in second_call_msgs:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    flat.append((m.get("role"), b.get("type")))

    # 至少出现一对 assistant:tool_use + user:tool_result。
    assert ("assistant", "tool_use") in flat
    assert ("user", "tool_result") in flat


# ── 2. max_iterations 防死循环 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_max_iterations_forces_synthesize(assembled_state, monkeypatch) -> None:
    """LLM 永远 tool_use → 达到 MAX_TOOL_ITERATIONS 后强制 llm_synthesize,不死循环。

    用一个小 MAX_TOOL_ITERATIONS(=2)+ 永远工具的脚本,验证:
    - 工具恰好执行 MAX_TOOL_ITERATIONS 次(tool_iteration == MAX)。
    - 之后 llm 条件边路由到 synthesize 而非 tool(不再 +1)。
    - 图正常终止(current_node == llm_synthesize),无 errors。
    """
    monkeypatch.setattr(chat_mod, "MAX_TOOL_ITERATIONS", 2)
    # 脚本永远 tool_use(足够多条以覆盖任何调用次数)。
    forever = [
        {"text": f"call {i}", "tool_use": {"name": "tool_a", "input": {}}}
        for i in range(20)
    ] + [{"text": "synthesized", "tool_use": None}]
    llm = _ScriptedLLM(forever)
    _state.llm_client = llm
    _state.tool_executor = _StubToolExecutor({"tool_a": "r"})

    g = _run_graph(GraphState(input="loop", agent_id="cli", session_id="s"))
    final = await g.run(GraphState(input="loop", agent_id="cli", session_id="s"))

    # 恰好 2 次工具(== MAX_TOOL_ITERATIONS),第3次 llm 决策被强制走 synthesize。
    assert final.tool_iteration == 2
    assert len(final.tool_use_history) == 2
    assert final.current_node == "llm_synthesize"
    assert final.errors == []


# ── 3. 单轮回归 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_turn_no_tool_goes_straight_to_synthesize(assembled_state) -> None:
    """LLM 首轮无 tool_use(needs_tool False)→ llm 条件边直接 llm_synthesize,
    不进 tool 节点。"""
    llm = _ScriptedLLM([
        {"text": "no tool needed", "tool_use": None},   # 首轮
        {"text": "synthesized answer", "tool_use": None},  # synthesize
    ])
    _state.llm_client = llm
    _state.tool_executor = _StubToolExecutor({})

    g = _run_graph(GraphState(input="hi", agent_id="cli", session_id="s"))
    final = await g.run(GraphState(input="hi", agent_id="cli", session_id="s"))

    assert final.tool_iteration == 0
    assert final.tool_use_history == []
    assert final.tool_results == []
    assert final.current_node == "llm_synthesize"
    assert final.output == "synthesized answer"
    # LLM 被调用 2 次(首轮决策 + synthesize),没有 tool 触发的额外轮。
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_single_turn_one_tool_then_synthesize(assembled_state) -> None:
    """一轮工具:首轮 tool_use → tool → 第2轮 llm 无 tool_use → synthesize。
    验证单轮工具仍是合法路径(tool→llm 回边后再判 synthesize)。"""
    llm = _ScriptedLLM([
        {"text": "need tool", "tool_use": {"name": "tool_a", "input": {"x": 1}}},
        {"text": "decided no more", "tool_use": None},  # tool 回边后无新工具 → synthesize
        {"text": "ANSWERED", "tool_use": None},          # synthesize 节点
    ])
    _state.llm_client = llm
    _state.tool_executor = _StubToolExecutor({"tool_a": "OUT"})

    g = _run_graph(GraphState(input="q", agent_id="cli", session_id="s"))
    final = await g.run(GraphState(input="q", agent_id="cli", session_id="s"))

    assert final.tool_iteration == 1
    assert len(final.tool_use_history) == 1
    assert final.tool_use_history[0]["tool_result"] == "OUT"
    assert final.current_node == "llm_synthesize"
    assert final.output == "ANSWERED"
    assert final.errors == []


# ── 4. 图结构断言(边拓扑) ──────────────────────────────────────────


def test_build_execution_graph_edge_topology() -> None:
    """静态断言图边拓扑符合多轮 loop 设计:
    - start → llm(无条件)
    - tool → llm(回边,不再 → llm_synthesize)
    - llm 条件边:targets 含 tool 与 llm_synthesize(无 __default__ 终止)
    - llm_synthesize 无出边(终止节点)
    """
    g = chat_mod._build_execution_graph()
    edges = g.list_edges()

    # 无条件边
    uncond = {(e["source"], e["target"]) for e in edges if e["type"] == "unconditional"}
    assert ("start", "llm") in uncond
    assert ("tool", "llm") in uncond
    # 关键:tool 不再直连 llm_synthesize(旧单轮设计)。
    assert ("tool", "llm_synthesize") not in uncond

    # 条件边:llm → {tool, llm_synthesize},无 __default__ 终止 sentinel。
    cond = [e for e in edges if e["type"] == "conditional" and e["source"] == "llm"]
    assert len(cond) == 1
    targets = cond[0]["targets"]
    assert targets.get("tool") == "tool"
    assert targets.get("llm_synthesize") == "llm_synthesize"
    assert "__default__" not in targets

    # llm_synthesize 无任何出边(终止)。
    sources = {e["source"] for e in edges}
    assert "llm_synthesize" not in sources
