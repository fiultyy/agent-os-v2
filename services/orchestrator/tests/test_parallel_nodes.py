"""ParallelNode / FanInNode / SubgraphNode — 语义固化测试。

固化 ``src/graph/__init__.py`` 已实现但生产图(chat.py)零消费的三类节点:

* :class:`ParallelNode` — 多分支 ``asyncio.gather`` 并行 + ``max_concurrency``
  semaphore 限流;每分支克隆独立 state;结果收集到
  ``state.parallel_results[name]``。
* :class:`FanInNode` — 汇聚 ``parallel_results[source]``,三种 merge_mode
  (``concat``/``best``/``aggregate``)。
* :class:`SubgraphNode` — 嵌套 ``StateGraph`` 执行,三种 merge_strategy
  (``replace``/``append``/``context``)。

pysqlite3 注入由 ``tests/conftest.py`` 统一处理(绕过 miniconda3 坏 sqlite3),
本文件不重复 patch。

此外含一个暴露 BFS walker join-barrier bug 的回归测试(diamond fan-in):
两个分支汇聚到同一 sink 时,sink 必须**只执行一次**。修复前 sink 被执行两次。
"""

import asyncio

import pytest

from src.graph import (
    StateGraph,
    GraphState,
    ParallelNode,
    FanInNode,
    SubgraphNode,
)
from src.graph.nodes import FunctionNode


# ── Mock node helpers ────────────────────────────────────────────────


def _recording_node(name: str, log: list[str], tag: str | None = None) -> FunctionNode:
    """Node that appends *tag* to output and records its name in *log* (order)."""
    label = tag or name

    async def _handler(state: GraphState) -> GraphState:
        log.append(name)
        state.output = (state.output or "") + label + "|"
        state.current_node = name
        return state

    return FunctionNode(name, _handler)


def _counting_node(name: str, counter: dict[str, int]) -> FunctionNode:
    """Node that increments counter[name] each time it runs (detects double-exec)."""

    async def _handler(state: GraphState) -> GraphState:
        counter[name] = counter.get(name, 0) + 1
        state.output = (state.output or "") + name + "|"
        state.current_node = name
        return state

    return FunctionNode(name, _handler)


# ── ParallelNode ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_node_runs_all_branches_and_collects_results() -> None:
    """All branches execute; outputs land in parallel_results[node.name]."""
    log: list[str] = []
    branches = {
        "alpha": [_recording_node("a1", log, "A1"), _recording_node("a2", log, "A2")],
        "beta": [_recording_node("b1", log, "B1")],
    }
    node = ParallelNode("par", branches)

    state = await node.execute(GraphState(input="x"))

    assert set(log) == {"a1", "a2", "b1"}
    collected = state.parallel_results["par"]
    assert {r["branch"] for r in collected} == {"alpha", "beta"}
    # Each branch record carries output + status.
    alpha = next(r for r in collected if r["branch"] == "alpha")
    assert "A1" in alpha["output"] and "A2" in alpha["output"]
    assert alpha["status"] == "running"  # branch_state.status set to "running"
    assert state.current_node == "par"


@pytest.mark.asyncio
async def test_parallel_node_clones_branch_state_isolation() -> None:
    """Each branch gets an independent clone; mutations don't leak to siblings."""
    async def _grow(state: GraphState) -> GraphState:
        state.context["seen_by"] = state.context.get("seen_by", []) + ["me"]
        return state

    branches = {"one": [FunctionNode("n1", _grow)], "two": [FunctionNode("n2", _grow)]}
    node = ParallelNode("par", branches)
    state = await node.execute(GraphState())

    # Parent state untouched by per-branch context mutations (clone isolation).
    assert "seen_by" not in state.context


@pytest.mark.asyncio
async def test_parallel_node_runs_branches_concurrently() -> None:
    """asyncio.gather means overlapping sleeps — total wall-time ≈ one sleep, not N."""
    async def _sleep_50ms(state: GraphState) -> GraphState:
        await asyncio.sleep(0.05)
        state.output = "done"
        return state

    branches = {
        str(i): [FunctionNode(f"n{i}", _sleep_50ms)] for i in range(4)
    }
    node = ParallelNode("par", branches)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await node.execute(GraphState())
    elapsed = loop.time() - start

    # 4×50ms serialised would be >=0.2s; concurrent should be well under that.
    assert elapsed < 0.18, f"branches not concurrent (elapsed={elapsed:.3f}s)"


@pytest.mark.asyncio
async def test_parallel_node_semaphore_limits_concurrency() -> None:
    """max_concurrency=1 serialises branch entry — at most 1 in-flight at a time."""
    inflight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _tracked(state: GraphState) -> GraphState:
        nonlocal inflight, peak
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        await asyncio.sleep(0.03)
        async with lock:
            inflight -= 1
        return state

    branches = {str(i): [FunctionNode(f"n{i}", _tracked)] for i in range(4)}
    node = ParallelNode("par", branches, max_concurrency=1)
    await node.execute(GraphState())

    assert peak == 1, f"semaphore allowed {peak} concurrent (expected 1)"


@pytest.mark.asyncio
async def test_parallel_node_unlimited_concurrency_no_semaphore() -> None:
    """max_concurrency=None → no semaphore → all branches overlap."""
    peak = 0
    inflight = 0
    lock = asyncio.Lock()

    async def _tracked(state: GraphState) -> GraphState:
        nonlocal inflight, peak
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        await asyncio.sleep(0.03)
        async with lock:
            inflight -= 1
        return state

    branches = {str(i): [FunctionNode(f"n{i}", _tracked)] for i in range(4)}
    node = ParallelNode("par", branches, max_concurrency=None)
    await node.execute(GraphState())

    assert peak == 4, f"expected 4 concurrent, got {peak}"


@pytest.mark.asyncio
async def test_parallel_node_branch_exception_isolated() -> None:
    """return_exceptions=True → one failing branch doesn't crash the gather.

    The error path records ``{"output": "", "status": "error", "error": ...}``
    WITHOUT a ``branch`` key (only the success path tags the branch). The good
    branch still completes and is tagged normally.
    """

    async def _boom(state: GraphState) -> GraphState:
        raise RuntimeError("boom")

    async def _ok(state: GraphState) -> GraphState:
        state.output = "ok"
        return state

    node = ParallelNode(
        "par",
        {"bad": [FunctionNode("bad", _boom)], "good": [FunctionNode("good", _ok)]},
    )
    state = await node.execute(GraphState())

    collected = state.parallel_results["par"]
    assert len(collected) == 2
    # Success branch is tagged.
    good = next(r for r in collected if r.get("branch") == "good")
    assert good["output"] == "ok"
    # Failure branch carries status=error + error string (no branch key).
    bad = next(r for r in collected if r.get("status") == "error")
    assert "boom" in bad["error"]


# ── FanInNode merge modes ────────────────────────────────────────────


def _state_with_parallel(parallel_name: str, results: list[dict]) -> GraphState:
    s = GraphState()
    s.parallel_results[parallel_name] = results
    return s


@pytest.mark.asyncio
async def test_fanin_concat_joins_outputs() -> None:
    state = _state_with_parallel(
        "par",
        [{"branch": "a", "output": "AAA"}, {"branch": "b", "output": "BBB"}],
    )
    out = await FanInNode("fan", "par", merge_mode="concat").execute(state)
    assert out.output == "AAA\n---\nBBB"


@pytest.mark.asyncio
async def test_fanin_best_picks_longest_output() -> None:
    state = _state_with_parallel(
        "par",
        [{"branch": "a", "output": "short"}, {"branch": "b", "output": "much longer"}],
    )
    out = await FanInNode("fan", "par", merge_mode="best").execute(state)
    assert out.output == "much longer"


@pytest.mark.asyncio
async def test_fanin_aggregate_structured() -> None:
    state = _state_with_parallel(
        "par",
        [{"branch": "a", "output": "AAA"}, {"branch": "b", "output": "BBB"}],
    )
    out = await FanInNode("fan", "par", merge_mode="aggregate").execute(state)
    assert out.output == "[a] AAA\n[b] BBB"
    assert out.context["fan_in_count"] == 2


@pytest.mark.asyncio
async def test_fanin_empty_results_placeholder() -> None:
    state = _state_with_parallel("par", [])
    out = await FanInNode("fan", "par", merge_mode="aggregate").execute(state)
    assert out.output == "[no parallel results to merge]"


@pytest.mark.asyncio
async def test_fanin_aggregate_all_empty_outputs() -> None:
    state = _state_with_parallel(
        "par", [{"branch": "a", "output": ""}, {"branch": "b", "output": ""}],
    )
    out = await FanInNode("fan", "par", merge_mode="aggregate").execute(state)
    assert out.output == "[all branches completed]"
    assert out.context["fan_in_count"] == 2


# ── ParallelNode + FanInNode end-to-end via direct execution ─────────


@pytest.mark.asyncio
async def test_parallel_then_fanin_pipeline() -> None:
    """Run a ParallelNode, then feed its results into a FanInNode."""
    log: list[str] = []
    branches = {
        "left": [_recording_node("l1", log, "LEFT")],
        "right": [_recording_node("r1", log, "RIGHT")],
    }
    state = await ParallelNode("par", branches).execute(GraphState())
    state = await FanInNode("fan", "par", merge_mode="aggregate").execute(state)
    assert "LEFT" in state.output and "RIGHT" in state.output


# ── SubgraphNode ─────────────────────────────────────────────────────


def _linear_subgraph(gid: str, tags: list[str]) -> StateGraph:
    """A 2-node linear sub-graph that concatenates *tags* into output."""
    sg = StateGraph(gid)

    async def _first(state: GraphState) -> GraphState:
        state.output = (state.output or "") + tags[0]
        state.current_node = "s1"
        return state

    async def _second(state: GraphState) -> GraphState:
        state.output = (state.output or "") + tags[1]
        state.current_node = "s2"
        return state

    sg.add_node("s1", FunctionNode("s1", _first))
    sg.add_node("s2", FunctionNode("s2", _second))
    sg.add_edge("s1", "s2")
    sg.set_entry_point("s1")
    return sg


@pytest.mark.asyncio
async def test_subgraph_context_strategy_stores_results() -> None:
    """context strategy: parent output untouched; sub-result stored by name.

    Note: the sub-graph runs on a clone of the parent state, so its output
    starts from the inherited parent output ("parent-") and then appends.
    """
    sg = _linear_subgraph("child", ["X", "Y"])
    node = SubgraphNode("sub", sg, merge_strategy="context")
    state = await node.execute(GraphState(output="parent-"))

    # Parent output untouched (context strategy doesn't rewrite output).
    assert state.output == "parent-"
    # Sub-graph output = inherited parent output + tags appended.
    assert state.subgraph_results["sub"]["output"] == "parent-XY"


@pytest.mark.asyncio
async def test_subgraph_replace_strategy_overwrites_parent() -> None:
    """replace strategy: parent output/messages replaced by sub-graph's.

    The sub-graph runs on a clone inheriting the parent output, so the
    replaced output is parent_output + sub-graph tags.
    """
    sg = _linear_subgraph("child", ["X", "Y"])
    node = SubgraphNode("sub", sg, merge_strategy="replace")
    state = await node.execute(GraphState(output="parent-"))

    assert state.output == "parent-XY"  # clone inherited "parent-", then XY


@pytest.mark.asyncio
async def test_subgraph_append_strategy_extends_messages() -> None:
    """append strategy: extends messages with sub-graph messages, then output.

    The clone inherits the parent messages, so ``extend(result.messages)``
    re-appends the inherited set, and ``result.output`` becomes a system msg.
    """
    sg = _linear_subgraph("child", ["X", "Y"])
    node = SubgraphNode("sub", sg, merge_strategy="append")
    state = await node.execute(GraphState(messages=[{"role": "user", "content": "hi"}]))

    # Original parent message preserved.
    assert state.messages[0] == {"role": "user", "content": "hi"}
    # Append added the sub-graph's (inherited) messages + a system message for output.
    assert state.messages[-1]["role"] == "system"
    assert "XY" in state.messages[-1]["content"]


@pytest.mark.asyncio
async def test_subgraph_runs_on_independent_clone() -> None:
    """Subgraph executes on a cloned state; parent context isn't polluted mid-run."""
    sg = _linear_subgraph("child", ["X", "Y"])
    node = SubgraphNode("sub", sg, merge_strategy="context")

    parent = GraphState(context={"keep": 1})
    await node.execute(parent)
    # Parent context preserved (replace strategy would merge, context does not).
    assert parent.context == {"keep": 1}


# ── Join-barrier regression: BFS walker fan-in ───────────────────────


@pytest.mark.asyncio
async def test_walker_fan_in_sink_executes_once() -> None:
    """Diamond: start -> {b, c} -> sink. The sink must run EXACTLY once.

    Regression for the BFS walker join-barrier bug: before the fix, both
    branches independently enqueued ``sink`` and it executed twice. With
    pending-queue dedup (``pending_set``) the second enqueue is skipped —
    yet already-departed nodes stay re-enqueueable, which is what keeps the
    production cyclic graph (chat.py tool->llm back-edge) working (reference
    counting would deadlock it: llm in-degree == 2).
    """
    counter: dict[str, int] = {}
    g = StateGraph("diamond")
    for n in ("start", "b", "c", "sink"):
        g.add_node(n, _counting_node(n, counter))
    g.add_edge("start", "b")
    g.add_edge("start", "c")
    g.add_edge("b", "sink")
    g.add_edge("c", "sink")
    g.set_entry_point("start")

    state = await g.run(GraphState())
    assert state.errors == []
    assert state.status == "done"

    # Every node ran exactly once — the crux of the join-barrier fix.
    assert counter == {"start": 1, "b": 1, "c": 1, "sink": 1}, counter
    # And the output reflects a single sink visit.
    assert state.output == "start|b|c|sink|"


@pytest.mark.asyncio
async def test_walker_single_branch_linear_still_one_pass() -> None:
    """Sanity: a plain linear chain must still work (in-degree=1 ⇒ no change)."""
    counter: dict[str, int] = {}
    g = StateGraph("linear")
    for n in ("a", "b", "c"):
        g.add_node(n, _counting_node(n, counter))
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.set_entry_point("a")

    state = await g.run(GraphState())
    assert state.errors == []
    assert counter == {"a": 1, "b": 1, "c": 1}
    assert state.output == "a|b|c|"
