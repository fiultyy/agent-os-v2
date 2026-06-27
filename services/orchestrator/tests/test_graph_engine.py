"""L2: graph engine — linear chain, conditional edges, and _resolve_next fan-out.

Covers the ``_resolve_next`` multi-edge fix in ``src/graph/__init__.py``. The
old body ``for edge in edges: return edge.route(state)`` returned on the FIRST
edge, so a fan-out node (multiple outgoing edges) could never reach any target
but the first. The walker (``run``/``resume``) is now BFS-frontier driven so
every branch is reachable while linear graphs behave identically.
"""

import pytest

from src.graph import StateGraph, GraphState
from src.graph.nodes import FunctionNode


def _node(name: str, marker: str | None = None) -> FunctionNode:
    """A node that appends its marker to ``state.output`` (records visit order)."""
    tag = marker or name

    async def _handler(state: GraphState) -> GraphState:
        state.output = (state.output or "") + tag + "|"
        state.current_node = name
        return state

    return FunctionNode(name, _handler)


@pytest.mark.asyncio
async def test_linear_four_nodes_executes_in_order() -> None:
    g = StateGraph("linear")
    for n in ("a", "b", "c", "d"):
        g.add_node(n, _node(n))
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", "d")
    g.set_entry_point("a")

    state = await g.run(GraphState())
    assert state.errors == []
    assert state.status == "done"
    assert state.output.split("|") == ["a", "b", "c", "d", ""]


@pytest.mark.asyncio
async def test_conditional_edge_routes_on_field() -> None:
    g = StateGraph("cond")
    for n in ("start", "yes", "no"):
        g.add_node(n, _node(n))
    g.add_conditional_edge(
        "start",
        targets={"go": "yes", "__default__": "no"},
        condition=lambda s: "go" if s.context.get("flag") else "",
    )
    g.set_entry_point("start")

    taken = await g.run(GraphState(context={"flag": True}))
    assert "yes" in taken.output and "no" not in taken.output

    skipped = await g.run(GraphState(context={"flag": False}))
    assert "no" in skipped.output and "yes" not in skipped.output


@pytest.mark.asyncio
async def test_resolve_next_collects_all_edges() -> None:
    """Fan-out: start -> {b, c}; both targets must be reachable (the bug)."""
    g = StateGraph("fan")
    for n in ("start", "b", "c", "sink"):
        g.add_node(n, _node(n))
    g.add_edge("start", "b")
    g.add_edge("start", "c")
    g.add_edge("b", "sink")
    g.add_edge("c", "sink")
    g.set_entry_point("start")

    # _resolve_next returns BOTH targets (the old code returned only the first).
    assert set(g._resolve_next("start", GraphState())) == {"b", "c"}

    state = await g.run(GraphState())
    assert state.errors == []
    reached = set(state.output.split("|")[:-1])
    assert reached == {"start", "b", "c", "sink"}


@pytest.mark.asyncio
async def test_resolve_next_empty_for_leaf_node() -> None:
    g = StateGraph("leaf")
    g.add_node("only", _node("only"))
    g.set_entry_point("only")
    assert g._resolve_next("only", GraphState()) == []
    state = await g.run(GraphState())
    assert state.status == "done"
