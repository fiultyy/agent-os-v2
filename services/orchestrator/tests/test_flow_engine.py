"""L2: harness FlowScheduler — render relay, dependency order, and DAG scheduling.

Mirrors ``test_graph_engine.py`` style (async, ``@pytest.mark.asyncio``, typed).
``_run_node`` is faked so ``run()``'s real DAG wiring (indegree drain,
``_schedule_dependents`` spawn, ``_render_message`` relay, status rollup) is
exercised without live harness/observe clients — only ``ObserveEmitter`` is
stubbed. Pure helpers (``_render_message``, ``_compute_indegree``,
``_on_tick_completed``) are tested directly.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from src.harness import flow as flow_mod
from src.harness.flow import FlowDef, FlowScheduler


class _FakeEmitter:
    """Stand-in for ObserveEmitter: connect/emit/close are no-ops."""

    async def connect(self) -> bool:
        return True

    async def emit(self, event: dict) -> None:
        return None

    async def close(self) -> None:
        return None


def _patch_run_node(sched: FlowScheduler, responses: dict) -> tuple[list, dict]:
    """Replace ``_run_node`` so ``run()`` drives its real scheduling without a
    harness client: render the message (exercises relay), record visit order +
    rendered text, mark the node completed with ``responses[id]``, then schedule
    dependents. Returns (order, rendered) for assertions."""
    order: list = []
    rendered: dict = {}

    async def fake(node) -> None:
        msg = sched._render_message(node.message)
        order.append(node.id)
        rendered[node.id] = msg
        resp = responses.get(node.id, "")
        st = sched.state.nodes[node.id]
        st["status"] = "completed"
        st["status_code"] = "success"
        st["response"] = resp
        sched._completed.add(node.id)
        await sched._schedule_dependents(node.id, resp)

    sched._run_node = fake  # type: ignore[method-assign]
    return order, rendered


# ── _render_message: placeholder rendering ──────────────────────────────

def test_render_replaces_completed_node_response() -> None:
    s = FlowScheduler(FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}]), "f")
    s.state.nodes["A"]["response"] = "hello"
    assert s._render_message("hi {node.A.response}!") == "hi hello!"


def test_render_unknown_or_unfinished_node_is_empty() -> None:
    s = FlowScheduler(FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}]), "f")
    # A still pending (response "") + ghost unknown → both placeholders empty,
    # literal "|" between them stays
    assert s._render_message("[{node.A.response}|{node.ghost.response}]") == "[|]"
    s.state.nodes["A"]["response"] = "x"
    assert s._render_message("{node.A.response}-{node.ghost.response}") == "x-"


def test_render_multiple_placeholders() -> None:
    s = FlowScheduler(
        FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"},
                       {"id": "B", "harness": "claw", "message": "m"}]), "f")
    s.state.nodes["A"]["response"] = "1"
    s.state.nodes["B"]["response"] = "2"
    assert s._render_message("{node.A.response}+{node.B.response}={node.A.response}{node.B.response}") == "1+2=12"


# ── indegree / graph validation ─────────────────────────────────────────

def test_compute_indegree_chain_and_diamond() -> None:
    chain = FlowDef(
        nodes=[{"id": n, "harness": "claw", "message": "m"} for n in "ABC"],
        edges=[{"from": "A", "to": "B"}, {"from": "B", "to": "C"}])
    assert FlowScheduler(chain, "f")._compute_indegree() == {"A": 0, "B": 1, "C": 1}

    diamond = FlowDef(
        nodes=[{"id": n, "harness": "claw", "message": "m"} for n in ("S", "L", "R", "M")],
        edges=[{"from": "S", "to": "L"}, {"from": "S", "to": "R"},
               {"from": "L", "to": "M"}, {"from": "R", "to": "M"}])
    assert FlowScheduler(diamond, "f")._compute_indegree() == {"S": 0, "L": 1, "R": 1, "M": 2}


def test_validate_graph_rejects_dup_id_and_unknown_node() -> None:
    dup = FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"},
                         {"id": "A", "harness": "claw", "message": "m"}])
    with pytest.raises(HTTPException):
        dup.validate_graph()

    dangling = FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}],
                       edges=[{"from": "A", "to": "Z"}])
    with pytest.raises(HTTPException):
        dangling.validate_graph()


# ── end-to-end scheduling via run() (fake _run_node, real DAG wiring) ────

@pytest.mark.asyncio
async def test_run_linear_chain_orders_by_dependency_and_relays(monkeypatch) -> None:
    monkeypatch.setattr(flow_mod, "ObserveEmitter", lambda **kw: _FakeEmitter())
    fd = FlowDef(
        nodes=[{"id": "A", "harness": "claw", "message": "say:apple"},
               {"id": "B", "harness": "claw", "message": "after-{node.A.response}"},
               {"id": "C", "harness": "claw", "message": "end-{node.B.response}"}],
        edges=[{"from": "A", "to": "B"}, {"from": "B", "to": "C"}])
    fd.validate_graph()
    s = FlowScheduler(fd, "f")
    order, rendered = _patch_run_node(s, {"A": "apple", "B": "banana", "C": "cherry"})
    await s.run()

    assert order == ["A", "B", "C"]                       # strict dependency order
    assert rendered["B"] == "after-apple"                 # relay of A's response
    assert rendered["C"] == "end-banana"                  # relay of B's response
    assert s.state.status == "completed"
    assert [s.state.nodes[n]["status"] for n in "ABC"] == ["completed"] * 3


@pytest.mark.asyncio
async def test_run_diamond_merge_waits_for_both_sources(monkeypatch) -> None:
    monkeypatch.setattr(flow_mod, "ObserveEmitter", lambda **kw: _FakeEmitter())
    fd = FlowDef(
        nodes=[{"id": "S", "harness": "claw", "message": "s"},
               {"id": "L", "harness": "claw", "message": "l"},
               {"id": "R", "harness": "claw", "message": "r"},
               {"id": "M", "harness": "claw",
                "message": "merge:{node.L.response}|{node.R.response}"}],
        edges=[{"from": "S", "to": "L"}, {"from": "S", "to": "R"},
               {"from": "L", "to": "M"}, {"from": "R", "to": "M"}])
    fd.validate_graph()
    s = FlowScheduler(fd, "f")
    order, rendered = _patch_run_node(s, {"S": "s", "L": "left", "R": "right", "M": "m"})
    await s.run()

    assert order[0] == "S" and order[-1] == "M"           # merge only after both sources done
    assert set(order[1:3]) == {"L", "R"}                  # the two sources (either order)
    assert rendered["M"] == "merge:left|right"            # multi-in node refs each source independently
    assert s.state.nodes["M"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_branch_dead_end_marks_unreached_skipped(monkeypatch) -> None:
    monkeypatch.setattr(flow_mod, "ObserveEmitter", lambda **kw: _FakeEmitter())
    fd = FlowDef(
        nodes=[{"id": "A", "harness": "claw", "message": "m"},
               {"id": "B", "harness": "claw", "message": "b"},
               {"id": "C", "harness": "claw", "message": "c"}],
        edges=[{"from": "A", "to": "B",
                "condition": {"field": "response", "op": "contains", "value": "go"}},
               {"from": "A", "to": "C"}])
    fd.validate_graph()
    s = FlowScheduler(fd, "f")
    order, _ = _patch_run_node(s, {"A": "stop", "B": "b", "C": "c"})  # "stop" lacks "go"
    await s.run()

    assert order == ["A", "C"]                            # A→B condition false, only C taken
    assert s.state.nodes["B"]["status"] == "skipped"      # never reached
    assert s.state.nodes["C"]["status"] == "completed"
    assert s.state.status == "completed"                  # skipped ≠ failed


@pytest.mark.asyncio
async def test_on_tick_completed_resolves_by_tick_then_session() -> None:
    """Emit interception: claude nodes resolve by tick_id, claw by session_id."""
    fd = FlowDef(nodes=[{"id": "A", "harness": "claude-code", "message": "m"},
                        {"id": "B", "harness": "claw", "message": "m"}])
    s = FlowScheduler(fd, "f")
    loop = asyncio.get_event_loop()
    fa, fb = loop.create_future(), loop.create_future()
    s._pending["A"] = {"future": fa, "match_type": "tick", "tick_id": "T1"}
    s._pending["B"] = {"future": fb, "match_type": "session", "session_id": "claw:s1"}

    s._on_tick_completed({"event_type": "tick_completed", "tick_id": "T1",
                          "session_id": "", "data": {"response": "ra", "status": "success"}})
    assert s.state.nodes["A"]["response"] == "ra"
    assert fa.done() and fa.result()["ok"] is True

    s._on_tick_completed({"event_type": "tick_completed", "tick_id": "",
                          "session_id": "claw:s1",
                          "data": {"response": "rb", "status": "error"}})
    assert s.state.nodes["B"]["status"] == "failed"
    assert fb.done() and fb.result()["ok"] is False
    assert "B" not in s._pending


# ── create_flow / run_flow endpoint functions ───────────────────────────

@pytest.mark.asyncio
async def test_create_flow_returns_structure_and_registers() -> None:
    from src.harness import routes
    fd = FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"},
                        {"id": "B", "harness": "claude-code", "message": "n"}],
                 edges=[{"from": "A", "to": "B"}])
    res = await routes.create_flow(fd)
    assert res["status"] == "created"
    assert res["nodes"] == ["A", "B"]
    assert res["edges"] == [{"from": "A", "to": "B"}]
    assert routes.get_flow(res["flow_id"]) is not None    # registered into the shared dict


@pytest.mark.asyncio
async def test_run_flow_404_when_missing() -> None:
    from src.harness import routes
    with pytest.raises(HTTPException) as ei:
        await routes.run_flow("flow_does_not_exist")
    assert ei.value.status_code == 404


# ── cancel(): idempotent state transition + run() cancelled path ────────

def test_cancel_marks_running_and_is_idempotent() -> None:
    """cancel() on a running flow → status='cancelled' + True; on any finished
    state (completed/failed/cancelled) → False (no-op)."""
    s = FlowScheduler(FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}]), "f")
    s.state.status = "running"
    assert s.cancel() is True
    assert s.state.status == "cancelled"
    # idempotent: already cancelled → False
    assert s.cancel() is False
    # completed/failed also no-op
    for done in ("completed", "failed"):
        s2 = FlowScheduler(FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}]), "f2")
        s2.state.status = done
        assert s2.cancel() is False
        assert s2.state.status == done


@pytest.mark.asyncio
async def test_run_cancelled_marks_inflight_cancelled_and_unreached_skipped(monkeypatch) -> None:
    """cancel() mid-run → in-flight node→cancelled, unreached dependent→skipped,
    flow status='cancelled'; run() itself is NOT cancelled (no CancelledError
    escapes) so its finish/cleanup block runs intact."""
    monkeypatch.setattr(flow_mod, "ObserveEmitter", lambda **kw: _FakeEmitter())
    fd = FlowDef(
        nodes=[{"id": "A", "harness": "claw", "message": "m"},
               {"id": "B", "harness": "claw", "message": "n"}],
        edges=[{"from": "A", "to": "B"}])
    fd.validate_graph()
    s = FlowScheduler(fd, "f")

    # A hangs forever at "running" (until cancel() cancels its task); B never starts.
    started = asyncio.Event()

    async def hang_node(node) -> None:
        s.state.nodes[node.id]["status"] = "running"
        started.set()
        await asyncio.Event().wait()   # never set → blocks until task cancelled

    s._run_node = hang_node  # type: ignore[method-assign]

    task = asyncio.create_task(s.run())
    await started.wait()            # A entered "running"
    await asyncio.sleep(0.01)       # let run()'s drain reach the gather await
    assert s.cancel() is True       # cancel in-flight A; drain then sees it done

    await task                      # run() finishes via cancelled-finish branch
    assert s.state.status == "cancelled"
    assert s.state.nodes["A"]["status"] == "cancelled"   # running → cancelled
    assert s.state.nodes["B"]["status"] == "skipped"     # never reached → skipped
    assert s.state.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_flow_endpoint_marks_and_409_on_finished() -> None:
    from src.harness import routes
    fd = FlowDef(nodes=[{"id": "A", "harness": "claw", "message": "m"}])
    fid = (await routes.create_flow(fd))["flow_id"]
    rec = routes.get_flow(fid)
    assert rec is not None
    rec["scheduler"].state.status = "running"   # simulate a running flow

    r = await routes.cancel_flow(fid)
    assert r == {"flow_id": fid, "status": "cancelled"}
    assert rec["scheduler"].state.status == "cancelled"

    with pytest.raises(HTTPException) as ei:    # idempotent → 409
        await routes.cancel_flow(fid)
    assert ei.value.status_code == 409

    with pytest.raises(HTTPException) as ei:    # missing → 404
        await routes.cancel_flow("flow_does_not_exist")
    assert ei.value.status_code == 404
