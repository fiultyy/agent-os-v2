"""Flow engine — turn chains / branches / DAG rebuilt on the trigger_turn primitive.

ADR-1 P2: 重编排在薄原语上重建。Flow 调度器不重新实现 turn,而是复用现有
harness clients(OpenClawClient / ClaudeClient)的 trigger_turn 语义——发 turn、
观察 tick_completed、评估出边 condition、触发依赖 turn、到终节点。

Graph topology:
- 链  A → B           (B 入度 1, A 完成后触发 B)
- 分支 A →{B if cond, C else}  (评估 A 出边 condition, 取满足的边)
- DAG 并行 start(gather) + 合并(B 入度>1, 所有入边完成才触发)

Event observation: harness clients 发完 turn 后 tick_completed 异步到达(经
client.emitter.emit)。Flow 在运行期间 wrap 该 emit: 匹配本节点 turn 的
tick_completed 即 resolve 节点 future 并捕获 response/status 供 condition 评估。

flow 级事件(flow_started/node_started/node_completed/flow_completed)harness_type
="flow",推 observe /ws/ingest。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .emit import ObserveEmitter
from .events import _base, _now

logger = logging.getLogger(__name__)

FLOW_HARNESS_TYPE = "flow"
OBSERVE_FLOW_HARNESS_ID = f"flow_engine_{uuid.uuid4().hex[:8]}"


# ── Flow DSL (pydantic validated) ────────────────────────────────────

class Condition(BaseModel):
    field_: str = Field(alias="field", description="response | status")
    op: str = "contains"   # contains | eq
    value: str

    model_config = {"populate_by_name": True}

    def evaluate(self, response: str, status: str) -> bool:
        if self.field_ == "status":
            target = status
        elif self.field_ == "response":
            target = response
        else:
            return False
        if self.op == "contains":
            return self.value in target
        if self.op == "eq":
            return self.value == target
        return False


class FlowNode(BaseModel):
    id: str
    harness: str   # claw | claude-code
    session_id: Optional[str] = None
    message: str
    agent_id: Optional[str] = None   # claw agentId override
    resume: bool = False              # claude --resume path


class FlowEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    condition: Optional[Condition] = None

    model_config = {"populate_by_name": True}


class FlowDef(BaseModel):
    nodes: List[FlowNode] = Field(..., min_length=1)
    edges: List[FlowEdge] = Field(default_factory=list)

    def validate_graph(self) -> None:
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise HTTPException(400, "duplicate node id")
        for e in self.edges:
            if e.from_ not in ids or e.to not in ids:
                raise HTTPException(
                    400, f"edge {e.from_}->{e.to} references unknown node")
        # ponytail: cycle 检测省略——flow 运行有节点级超时兜底,环只会卡住自己
        # 不影响其它 flow;add 显式 cycle 检测 when flow 复杂到肉眼看不出环


# ── Flow-level event constructors (harness_type="flow") ──────────────

def flow_event(event_type: str, flow_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """flow_* 事件,session_id = flow_id,harness_type=flow。

    ponytail: observe 的 EventType enum 只有 tick_*/tool_*/branch_*/token_*
    (services/observe/src/events.py),未知 event_type 在 ObserveEvent.from_dict
    抛 ValueError 被静默丢。红线禁改 observe,所以 flow/node 事件 wire 成
    tick_completed(observe 接受的唯一泛化"完成"类型),真实语义塞进
    data.flow_event + data.flow_payload。TUI/消费者按 data.flow_event 分类。
    """
    return {
        "event_id": str(uuid.uuid4()),
        "harness_type": FLOW_HARNESS_TYPE,
        "harness_id": OBSERVE_FLOW_HARNESS_ID,
        "session_id": flow_id,
        "tick_id": flow_id,
        "event_type": "tick_completed",
        "data": {
            "status": "success",
            "response": "",
            "flow_event": event_type,
            "flow_payload": data,
        },
        "timestamp": _now(),
    }


# ── Flow state ───────────────────────────────────────────────────────

# node 状态: pending | running | completed | failed | skipped
NODE_DONE_STATES = {"completed", "failed", "skipped"}


class FlowState:
    """可序列化的 flow 运行状态(GET /h/flows/{id} 返回)。"""

    def __init__(self, flow_def: FlowDef, flow_id: str):
        self.flow_id = flow_id
        self.status = "pending"   # pending | running | completed | failed
        self.nodes: Dict[str, Dict[str, Any]] = {
            n.id: {"id": n.id, "status": "pending", "response": "",
                   "status_code": "", "error": ""}
            for n in flow_def.nodes
        }
        self.started_at = _now()
        self.finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "status": self.status,
            "nodes": self.nodes,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── Flow registry ────────────────────────────────────────────────────

_flows: Dict[str, Dict[str, Any]] = {}   # flow_id -> {def, state, scheduler}


def get_flow(flow_id: str) -> Optional[Dict[str, Any]]:
    return _flows.get(flow_id)


# ── FlowScheduler ────────────────────────────────────────────────────

class FlowScheduler:
    """Runs a FlowDef as an asyncio task graph over the trigger_turn primitive.

    Each node = one trigger_turn via the existing harness client. Completion is
    observed by wrapping client.emitter.emit (both clients route every event
    through it). Match is by tick_id when known (claude), else by session
    (claw: one in-flight node per session during the run).
    """

    def __init__(self, flow_def: FlowDef, flow_id: str):
        self.flow_def = flow_def
        self.flow_id = flow_id
        self.state = FlowState(flow_def, flow_id)
        # node_id -> {future, match_key}
        self._pending: Dict[str, Dict[str, Any]] = {}
        # session_key(harness:session) -> set of node_ids awaiting completion on it
        self._session_watchers: Dict[str, set] = {}
        # 入度计数:node_id -> 剩余未完成入边数
        self._indegree: Dict[str, int] = self._compute_indegree()
        self._completed: set = set()
        self._node_tasks: set = set()   # all in-flight node tasks (for drain)
        self._wrapped_emitters: List[Any] = []   # (emitter, original_emit)
        self._task: Optional[asyncio.Task] = None
        # persistent emitter for flow_* events (connect once in run(), close at end)
        self._flow_emitter: Optional[ObserveEmitter] = None

    def _compute_indegree(self) -> Dict[str, int]:
        ind = {n.id: 0 for n in self.flow_def.nodes}
        for e in self.flow_def.edges:
            ind[e.to] = ind.get(e.to, 0) + 1
        return ind

    # ── emit interception ──────────────────────────────────────────

    def _wrap_emitter(self, client: Any) -> None:
        """Wrap client.emitter.emit to intercept tick_completed for this run.

        ponytail: monkey-patch per run, unwrapped on finish. A callback hook
        on both clients would be cleaner, but claw has on_event while claude
        routes straight to emitter — wrapping emit is the single chokepoint
        both clients share.
        """
        emitter: ObserveEmitter = client.emitter
        if getattr(emitter, "_flow_wrapped", False):
            return   # already wrapped (shared session across nodes)

        original = emitter.emit

        async def flow_emit(event: Dict[str, Any]) -> None:
            # forward to observe always
            try:
                await original(event)
            except Exception:
                pass
            if event.get("event_type") != "tick_completed":
                return
            self._on_tick_completed(event)

        emitter.emit = flow_emit   # type: ignore[method-assign]
        emitter._flow_wrapped = True   # type: ignore[attr-defined]
        self._wrapped_emitters.append((emitter, original))

    def _unwrap_emitters(self) -> None:
        for emitter, original in self._wrapped_emitters:
            try:
                emitter.emit = original   # type: ignore[method-assign]
                emitter._flow_wrapped = False   # type: ignore[attr-defined]
            except Exception:
                pass
        self._wrapped_emitters.clear()

    def _on_tick_completed(self, event: Dict[str, Any]) -> None:
        """Resolve any pending node whose turn this tick_completed matches."""
        tick_id = event.get("tick_id", "")
        session_id = event.get("session_id", "")
        ht = event.get("harness_type", "")
        data = event.get("data", {})
        response = data.get("response", "")
        status = data.get("status", "")

        # 1. exact tick_id match (claude)
        if tick_id:
            for node_id, info in list(self._pending.items()):
                if info.get("match_type") == "tick" and info.get("tick_id") == tick_id:
                    self._resolve(node_id, status, response)
                    return

        # 2. session match (claw: tick_id not returned by send_message)
        for node_id, info in list(self._pending.items()):
            if info.get("match_type") == "session" and info.get("session_id") == session_id:
                self._resolve(node_id, status, response)
                return

    def _resolve(self, node_id: str, status: str, response: str) -> None:
        info = self._pending.pop(node_id, None)
        if not info:
            return
        node_state = self.state.nodes[node_id]
        node_state["status"] = "completed" if status == "success" else "failed"
        node_state["status_code"] = status
        node_state["response"] = response
        info["future"].set_result({
            "status": status, "response": response,
            "ok": status == "success",
        })

    # ── node execution ─────────────────────────────────────────────

    _NODE_REF = re.compile(r"\{node\.([A-Za-z0-9_-]+)\.response\}")

    def _render_message(self, msg: str) -> str:
        """渲染 {node.<id>.response} 占位 → 该 node 已捕获的 response(词语接龙等
        chain:每步 message 引用上一步输出)。未完成/未知 node → 空串。

        ponytail: 正则单占位,不支持变换/截取/拼接;多入边 DAG 各引用自己源 node。
        升级: jinja2(filter/slice/join)if message 组合复杂到手写不值。
        """
        def repl(m: "re.Match[str]") -> str:
            nid = m.group(1)
            return self.state.nodes.get(nid, {}).get("response", "") or ""
        return self._NODE_REF.sub(repl, msg)

    async def _run_node(self, node: FlowNode) -> None:
        # import here to avoid circular import (routes imports flow)
        from .routes import _get_client, _create_claw, _create_claude, _sessions, _key

        self.state.nodes[node.id]["status"] = "running"
        # 渲染 {node.<id>.response} 占位 → 已完成 node 的输出(词语接龙每步接上一步
        # 的词)。依赖 node 在 _schedule_dependents 里早已 completed,response 已入 state。
        msg = self._render_message(node.message)
        await self._emit_flow("node_started", {
            "node_id": node.id, "harness": node.harness,
            "session_id": node.session_id or "", "message": msg[:200],
        })

        # ensure session exists (create if missing)
        sid = node.session_id
        client = _get_client(node.harness, sid) if sid else None
        created = False
        if client is None:
            if node.harness == "claw":
                agent = node.agent_id or (sid or "main")
                if not sid:
                    sid = agent if ":" in agent else f"agent:{agent}:main"
                client = await _create_claw(sid, agent)
            elif node.harness == "claude-code":
                if not sid:
                    sid = str(uuid.uuid4().hex[:12])
                client = await _create_claude(sid, None)
            else:
                raise HTTPException(400, f"unknown harness '{node.harness}'")
            _sessions[_key(node.harness, sid)] = {
                "client": client, "session_id": sid,
                "harness_type": node.harness, "agent_id": node.agent_id,
            }
            created = True
        node.session_id = sid

        # wrap emitter to observe tick_completed BEFORE sending the turn
        self._wrap_emitter(client)

        # set up completion watcher
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        if node.harness == "claude-code":
            # claude turn() returns tick_id synchronously
            result = await client.turn(msg, resume=node.resume)
            self._pending[node.id] = {
                "future": fut, "match_type": "tick",
                "tick_id": result["tick_id"],
            }
        else:  # claw: send_message returns nothing; match by session
            if not getattr(client, "running", False):
                # claw client not connected yet — short poll
                for _ in range(50):
                    if getattr(client, "running", False):
                        break
                    await asyncio.sleep(0.1)
            await client.send_message(
                msg, agent_id=node.agent_id)
            self._pending[node.id] = {
                "future": fut, "match_type": "session",
                "session_id": sid,
            }

        # wait for tick_completed (with per-node timeout)
        try:
            done = await asyncio.wait_for(fut, timeout=NODE_TIMEOUT)
        except asyncio.TimeoutError:
            done = {"status": "error", "response": "node timeout",
                    "ok": False}
            node_state = self.state.nodes[node.id]
            node_state["status"] = "failed"
            node_state["error"] = "node timeout"
            self._pending.pop(node.id, None)

        await self._emit_flow("node_completed", {
            "node_id": node.id, "status": done.get("status", "error"),
            "response": str(done.get("response", ""))[:200],
            "ok": done.get("ok", False),
        })
        self._completed.add(node.id)

        # schedule dependents if node succeeded
        if done.get("ok", False):
            await self._schedule_dependents(node.id, done["response"])

    async def _schedule_dependents(self, node_id: str, response: str) -> None:
        status = self.state.nodes[node_id]["status_code"]
        for edge in self.flow_def.edges:
            if edge.from_ != node_id:
                continue
            # branch: only traverse edges whose condition holds (or none)
            if edge.condition is not None and \
                    not edge.condition.evaluate(response, status):
                continue
            self._indegree[edge.to] -= 1
            if self._indegree[edge.to] <= 0:
                dep = next(n for n in self.flow_def.nodes if n.id == edge.to)
                self._spawn_node(dep)

    def _spawn_node(self, node: FlowNode) -> None:
        """Start a node task, tracked in self._node_tasks for draining."""
        t = asyncio.create_task(self._run_node(node))
        self._node_tasks.add(t)
        t.add_done_callback(self._node_tasks.discard)

    # ── top-level run ──────────────────────────────────────────────

    async def run(self) -> None:
        self.state.status = "running"
        # persistent flow emitter: connect once, reuse for all flow_* events
        self._flow_emitter = ObserveEmitter(
            harness_type=FLOW_HARNESS_TYPE,
            harness_id=OBSERVE_FLOW_HARNESS_ID,
            session_id=self.flow_id,
        )
        connected = await self._flow_emitter.connect()
        if not connected:
            logger.warning("flow emitter connect failed; flow_* events will be dropped")

        await self._emit_flow("flow_started", {
            "flow_id": self.flow_id,
            "nodes": [n.id for n in self.flow_def.nodes],
            "edges": [{"from": e.from_, "to": e.to} for e in self.flow_def.edges],
        })

        starts = [n for n in self.flow_def.nodes if self._indegree.get(n.id, 0) == 0]
        for n in starts:
            self._spawn_node(n)

        # drain the whole DAG: keep waiting until no node task is in flight.
        # handles chains/branches/DAG uniformly (dependents spawn into the set).
        while self._node_tasks:
            await asyncio.gather(*list(self._node_tasks), return_exceptions=True)
            # purge finished tasks explicitly: relying solely on each task's
            # done-callback to discard() live-locks when several nodes finish in
            # the same tick — gather() of already-done tasks returns without
            # yielding, so the callbacks get starved and _node_tasks never
            # drains (hit on fan-out: A→{B,C} with no merge).
            self._node_tasks -= {t for t in list(self._node_tasks) if t.done()}

        # any node that never got reached (branch dead-end / DAG left behind)
        for node in self.flow_def.nodes:
            if self.state.nodes[node.id]["status"] == "pending":
                self.state.nodes[node.id]["status"] = "skipped"

        self.state.status = "failed" if any(
            self.state.nodes[n.id]["status"] == "failed"
            for n in self.flow_def.nodes
        ) else "completed"
        self.state.finished_at = _now()

        await self._emit_flow("flow_completed", {
            "flow_id": self.flow_id, "status": self.state.status,
            "nodes": {nid: s["status"] for nid, s in self.state.nodes.items()},
        })
        self._unwrap_emitters()
        if self._flow_emitter is not None:
            await self._flow_emitter.close()
            self._flow_emitter = None

    def start_background(self) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self.run())
        return self._task

    # ── flow-level event push ──────────────────────────────────────

    async def _emit_flow(self, event_type: str, data: Dict[str, Any]) -> None:
        """Push a flow_* event through the persistent flow emitter."""
        ev = flow_event(event_type, self.flow_id, data)
        if self._flow_emitter is not None:
            await self._flow_emitter.emit(ev)
        else:
            logger.info("flow event (no emitter): %s %s", event_type, data)


NODE_TIMEOUT = 180.0   # ponytail: single global cap; per-node timeout when slow agents warrant it


# ── self-check ───────────────────────────────────────────────────────

if __name__ == "__main__":
    # condition + indegree logic self-check (no harness/observe needed)
    c = Condition(field="response", op="contains", value="2")
    assert c.evaluate("the answer is 2", "success") is True
    assert c.evaluate("the answer is 3", "success") is False
    c2 = Condition(field="status", op="eq", value="success")
    assert c2.evaluate("x", "success") is True
    assert c2.evaluate("x", "error") is False

    fd = FlowDef(
        nodes=[{"id": "A", "harness": "claw", "message": "m"},
               {"id": "B", "harness": "claude-code", "message": "n"}],
        edges=[{"from": "A", "to": "B"}],
    )
    fd.validate_graph()
    sched = FlowScheduler(fd, "test")
    assert sched._indegree == {"A": 0, "B": 1}, sched._indegree
    assert sched._compute_indegree()["B"] == 1

    # branch condition
    fb = FlowDef(
        nodes=[{"id": "A", "harness": "claw", "message": "m"},
               {"id": "B", "harness": "claw", "message": "b"},
               {"id": "C", "harness": "claw", "message": "c"}],
        edges=[{"from": "A", "to": "B",
                "condition": {"field": "response", "op": "contains", "value": "2"}},
               {"from": "A", "to": "C"}],
    )
    fb.validate_graph()
    sb = FlowScheduler(fb, "test2")
    assert sb._indegree == {"A": 0, "B": 1, "C": 1}
    print("flow self-check OK")
