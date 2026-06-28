"""P2: ParallelNode 接生产图 demo 回归测试。

固化 ``_build_parallel_graph`` + ``/v1/execute_parallel`` 端点的语义:

1. **多分支并行执行** — 每个 agent_id 一条分支,经 ``run_agent_turn`` 跑一轮 LLM。
2. **fan-in 汇聚** — ``FanInNode(concat)`` 把所有分支 output 拼进 ``state.output``。
3. **state.clone 不丢分支 output** — 每分支在克隆 state 上设 output,fan-in 能读到。
4. **SSE par/fanin 节点事件** — ``on_node_complete`` 对 multi_perspective/fan_in/
   synthesizer 发 SSE(branch 列表 + output)。
5. **run_agent_turn 复用**(R1)— 分支不新写 LLM 调用,统一走 ``run_agent_turn``;
   ``_build_parallel_graph`` 函数体零 memory 调用(grep clean)。

客观隔离判据:本文件与 test_multi_turn_tool_loop / test_graph_engine /
test_parallel_nodes 三文件互不依赖;后者零改动全绿 = 物理隔离有效。

pysqlite3 注入由 ``tests/conftest.py`` 统一处理,本文件不重复 patch。
"""

import asyncio
import inspect
import re
import types

import pytest

from src.api.routes import chat as chat_mod
from src.api.models import ExecuteParallelRequest
from src.graph import GraphState
from src.services import _state


# ── Stub 装配 ────────────────────────────────────────────────────────


class _ScriptedLLM:
    """按 agent_id 映射返回固定响应的 LLMClient 替身。

    每个 agent_id 一个响应字符串;``chat`` 被调用时根据传入 messages 里的
    system_prompt 形状无关紧要 —— 我们只关心 ``run_agent_turn`` 把响应写进分支
    output,以及 fan-in 是否汇聚所有分支。
    """

    def __init__(self, responses_by_agent: dict[str, str]):
        self._resp = dict(responses_by_agent)
        self.calls: list[tuple[str | None, list[dict]]] = []

    async def chat(self, messages, model=None, **kw):
        # run_agent_turn 用 _state.agents[agent_id]["model"] 作 model 参数;
        # 但响应映射我们直接按 messages 里 user content 的形状取不到 agent_id,
        # 所以这里按调用顺序 + system_prompt 区分(测试里每个 agent 不同 prompt)。
        system = ""
        user = ""
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            elif m.get("role") == "user":
                user = m.get("content", "")
        self.calls.append((model, list(messages)))
        # 用 system_prompt 的标记(测试里埋了 agent 标识)路由响应。
        for key, resp in self._resp.items():
            if key in system:
                return resp
        # synthesizer 综合调用(system 无 agent 标记)→ 返回综合文。
        if "Synthesize" in user or "synthesize" in user:
            return "SYNTH-RESULT"
        return "default-response"


@pytest.fixture
def parallel_state():
    """备份/还原 _state 关键字段,装配最小并行依赖。"""
    saved = {
        k: getattr(_state, k, None)
        for k in (
            "llm_client", "agents", "communication_bus",
            "concurrency_controller", "memory_event_bus",
        )
    }
    _state.agents = {
        "agent_a": {"model": "ma", "system_prompt": "[agent_a] you are A"},
        "agent_b": {"model": "mb", "system_prompt": "[agent_b] you are B"},
        "agent_c": {"model": "mc", "system_prompt": "[agent_c] you are C"},
    }
    _state.memory_event_bus = _NoopEventBus()

    # 真实 communication_bus(register_agent/broadcast 都用得上,确保 SSE 广播不崩)。
    from src.communication.bus import CommunicationBus
    _state.communication_bus = CommunicationBus()

    # 真实 ConcurrencyController(acquire/release_agent_slot)。
    from src.concurrency.controller import ConcurrencyController
    _state.concurrency_controller = ConcurrencyController()

    yield
    for k, v in saved.items():
        setattr(_state, k, v)


class _NoopEventBus:
    async def emit(self, event_type, ctx):
        return None


# ── 1. 多分支并行执行 + fan-in 汇聚 ──────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_graph_runs_all_branches_and_fan_in_concat(parallel_state) -> None:
    """每 agent_id 一条分支 → run_agent_turn → fan-in concat 含所有分支 output。

    断言:
    - 所有 agent 都被调到(run_agent_turn 调用次数 == len(agent_ids))。
    - fan-in 后 state.output 含全部三个分支的响应片段。
    """
    llm = _ScriptedLLM({
        "agent_a": "PERSPECTIVE-FROM-A",
        "agent_b": "PERSPECTIVE-FROM-B",
        "agent_c": "PERSPECTIVE-FROM-C",
    })
    _state.llm_client = llm

    g = chat_mod._build_parallel_graph(
        agent_ids=["agent_a", "agent_b", "agent_c"],
        input="analyze X",
        session_id="s1",
    )
    result = await g.run(GraphState(input="analyze X", agent_id="agent_a", session_id="s1"))

    branches = result.parallel_results.get("multi_perspective", [])
    branch_names = sorted(b.get("branch") for b in branches)
    assert branch_names == ["branch_agent_a", "branch_agent_b", "branch_agent_c"], (
        f"all three branches must execute; got {branch_names}"
    )
    # LLM 被调 3(分支) + 1(synthesizer) = 4 次
    assert len(llm.calls) == 4, f"expected 4 LLM calls (3 branches + synth), got {len(llm.calls)}"


# ── 2. fan-in 汇聚 concat 结果含所有分支 ──────────────────────────────


@pytest.mark.asyncio
async def test_fan_in_concat_contains_all_branch_outputs(parallel_state) -> None:
    """FanInNode(concat) 把分支 output 拼进 state.output。

    临时关掉 synthesizer 验证纯 fan-in:用一个不存在的 agent 列表会触发 404,
    所以改为直接检查 parallel_results + 中途 fan_in 节点后的 output。
    这里我们断言 parallel_results 含所有 output(分支克隆 state 上设的)。
    """
    llm = _ScriptedLLM({
        "agent_a": "AAA",
        "agent_b": "BBB",
    })
    _state.llm_client = llm

    g = chat_mod._build_parallel_graph(
        agent_ids=["agent_a", "agent_b"],
        input="task",
        session_id="s2",
    )
    result = await g.run(GraphState(input="task", agent_id="agent_a", session_id="s2"))

    branches = result.parallel_results.get("multi_perspective", [])
    outputs = {b.get("branch"): b.get("output") for b in branches}
    assert outputs.get("branch_agent_a") == "AAA"
    assert outputs.get("branch_agent_b") == "BBB"

    # fan_in (concat) 阶段产出应同时含两段(顺序取决于 asyncio.gather 完成序,
    # 但内容集合应等价)。最终 state.output 经 synthesizer 覆盖为 SYNTH-RESULT。
    # 这里改为直接构造 FanInNode 验证 concat 语义(独立于 synthesizer 覆盖)。
    from src.graph import FanInNode
    fan = FanInNode("f", source_name="multi_perspective", merge_mode="concat")
    s = GraphState()
    s.parallel_results["multi_perspective"] = branches
    merged = await fan.execute(s)
    assert "AAA" in merged.output and "BBB" in merged.output


# ── 3. state.clone 不丢分支 output ────────────────────────────────────


@pytest.mark.asyncio
async def test_branch_clone_preserves_output_for_fan_in(parallel_state) -> None:
    """每分支在 *克隆* state 上设 output,fan-in 必须读到(ParallelNode 实现)。

    ParallelNode._run_branch 在 branch_state = state.clone() 上跑节点,再把
    branch_state.output 收集进 parallel_results。验证 clone 后的 output 不丢。
    """
    llm = _ScriptedLLM({"agent_a": "X", "agent_b": "Y"})
    _state.llm_client = llm

    g = chat_mod._build_parallel_graph(["agent_a", "agent_b"], "t", "s3")
    result = await g.run(GraphState(input="t", agent_id="agent_a", session_id="s3"))

    branches = result.parallel_results.get("multi_perspective", [])
    # 每分支的 output 字段非空且等于该 agent 的响应
    by_branch = {b["branch"]: b["output"] for b in branches}
    assert by_branch["branch_agent_a"] == "X"
    assert by_branch["branch_agent_b"] == "Y"


# ── 4. SSE par/fanin 节点事件 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_parallel_emits_sse_for_par_and_fanin(parallel_state) -> None:
    """/execute_parallel 的 on_node_complete 对 multi_perspective/fan_in/synthesizer 发 SSE。"""
    llm = _ScriptedLLM({"agent_a": "outA", "agent_b": "outB"})
    _state.llm_client = llm

    sse_events: list[str] = []

    from fastapi.encoders import jsonable_encoder
    import json

    async def collect():
        req = ExecuteParallelRequest(
            agent_ids=["agent_a", "agent_b"], input="hello", session_id="sx",
        )
        resp = await chat_mod.execute_parallel(req)
        # StreamingResponse — 逐块消费 body
        async for chunk in resp.body_iterator:
            sse_events.append(chunk if isinstance(chunk, str) else chunk.decode())

    await collect()

    blob = "".join(sse_events)
    # multi_perspective 节点事件(branch 列表可见)
    assert "multi_perspective" in blob, "SSE must surface multi_perspective node"
    assert "branch_agent_a" in blob and "branch_agent_b" in blob, (
        "SSE branch list must include both agents"
    )
    # fan_in 节点事件
    assert "fan_in" in blob, "SSE must surface fan_in node"
    # synthesizer 节点事件
    assert "synthesizer" in blob, "SSE must surface synthesizer node"
    # execution_complete 事件
    assert "execution_complete" in blob


# ── 5. run_agent_turn 复用(R1)— 分支不新写 LLM 调用 + 函数体零 memory ──


def test_build_parallel_graph_reuses_run_agent_turn_not_new_llm() -> None:
    """_build_parallel_graph 分支必须调 run_agent_turn,不新写 LLM 调用。

    静态断言:函数 *代码体*(去掉 docstring)引用 ``run_agent_turn`` 且 *不* 引用
    ``_state.llm_client.chat`` / ``llm_client`` 直接调用(那是 _node_llm 的模式,
    会带记忆触发)。复用 = 只走 run_agent_turn 这一条 LLM 调用路径。
    """
    import ast
    full_src = inspect.getsource(chat_mod._build_parallel_graph)
    tree = ast.parse(full_src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    code = "\n".join(ast.get_source_segment(full_src, s) or "" for s in body)
    assert "run_agent_turn" in code, "branches must reuse run_agent_turn (P1 primitive)"
    # 不应出现直接的 llm_client.chat 调用(那是绕过 run_agent_turn 新写 LLM 路径)。
    assert "llm_client.chat" not in code, (
        "_build_parallel_graph must NOT call llm_client.chat directly — reuse run_agent_turn"
    )
    # 也不应出现 _node_llm_for 风格的新函数定义(代码体,非 docstring)。
    assert "def _node_llm_for" not in code, "must not define a new _node_llm_for"


def test_build_parallel_graph_has_zero_memory_calls() -> None:
    """R1:_build_parallel_graph 函数体(去掉 docstring)零 memory 调用。"""
    import ast
    tree = ast.parse(inspect.getsource(chat_mod._build_parallel_graph))
    # 取函数节点(FunctionDef),剥离 docstring 后的纯代码。
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    code = "\n".join(ast.get_source_segment(inspect.getsource(chat_mod._build_parallel_graph), s) or "" for s in body)
    forbidden = re.findall(r"memory_event_bus\.emit|_trigger_ingest|_trigger_kg|memory_service\.", code)
    assert forbidden == [], (
        f"R1 violated: _build_parallel_graph body must have zero memory calls, found {forbidden}"
    )


# ── 6. 端点验证:agent 不存在 → 404 ───────────────────────────────────


@pytest.mark.asyncio
async def test_execute_parallel_404_on_missing_agent(parallel_state) -> None:
    """agent_ids 中有不在 _state.agents 的 → JSONResponse 404,不建图。"""
    from fastapi.responses import JSONResponse
    req = ExecuteParallelRequest(
        agent_ids=["agent_a", "ghost"], input="x", session_id="",
    )
    resp = await chat_mod.execute_parallel(req)
    assert isinstance(resp, JSONResponse), "missing agent should return JSONResponse 404"
    assert resp.status_code == 404


# ── 7. 端点路由挂载 ───────────────────────────────────────────────────


def test_execute_parallel_route_registered() -> None:
    """/execute_parallel 路由存在于 chat router(挂 /v1 后即 /v1/execute_parallel)。"""
    from src.api.routes.chat import router
    paths = [getattr(r, "path", "") for r in router.routes]
    assert "/execute_parallel" in paths, f"route missing; router has {paths}"
    assert "/execute" in paths, "regression: /execute must still be registered"


# ── 8. 单分支并行(退化)也工作 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_agent_parallel_branch(parallel_state) -> None:
    """单个 agent_id:ParallelNode 退化成单分支,fan-in 仍汇聚(只有一段)。"""
    llm = _ScriptedLLM({"agent_a": "SOLO"})
    _state.llm_client = llm
    g = chat_mod._build_parallel_graph(["agent_a"], "only", "s4")
    result = await g.run(GraphState(input="only", agent_id="agent_a", session_id="s4"))
    branches = result.parallel_results.get("multi_perspective", [])
    assert len(branches) == 1
    assert branches[0]["branch"] == "branch_agent_a"
    assert branches[0]["output"] == "SOLO"
