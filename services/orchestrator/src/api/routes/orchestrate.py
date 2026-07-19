"""POST /v1/orchestrate — P3 多 agent 编排集成终点(SSE)。

接 :class:`OrchestrateRequest`,校验 ``orchestrator_agent_id`` 在
``_state.agents``,用 :func:`_build_multi_agent_graph` 构造生产图:

    multi_agent(ParallelNode: AgentWorkerNode × N 角色)
        ──▶ fan_in(FanInNode aggregate)
        ──▶ synthesizer(run_agent_turn(orchestrator_id) 综合)

每个 AgentWorkerNode 真实 spawn 一个临时 subagent(create_subagent →
run_agent_turn → teardown),与 P2 ``/execute_parallel``(预存 agent 并行)本质
不同。SSE 加 ``multi_agent`` / ``fan_in`` / ``synthesizer`` 节点名分支,使
fan-out / fan-in 阶段对客户端可见。

红线
----
R1(记忆单点落库):orchestrate 路径记忆只 fan-in 后主 agent(orchestrator)单点
    落库 —— 分支(subagent)零 memory。本端点 *不* emit memory 事件(区别于
    ``/execute`` 的 SESSION_START emit);综合轮走 run_agent_turn(orchestrator),
    记忆由 orchestrator 自身执行图(若挂)负责,本端点不强制。
R2(物理隔离):零主路径 diff —— 新 router + 新 request + 新 graph builder。
R7(独立 bus):本端点 *不* 触碰 ``_state.memory_event_bus``。
R8(并发复用):经 ``_state.concurrency_controller.acquire_agent_slot``(为
    orchestrator 取槽),不自造并发原语。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.models import OrchestrateRequest
from src.graph import GraphState
from src.harness.emit import ObserveEmitter
from src.harness.events import _now
from src.orchestration.multi_agent_graph import _build_multi_agent_graph
from src.services import _state
from src.services.agent_manager import (
    create_subagent as _am_create_subagent,
    teardown_subagent as _am_teardown_subagent,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 模块级固定 harness_id(与 flow.py OBSERVE_FLOW_HARNESS_ID 同模式)。
# harness_type="orchestrate"(新,非复用 "flow"):flow 语义(flow_id 生命周期)≠
# orchestrate(session 生命周期),复用会混(见 plan Part 3.2)。
_ORCH_HARNESS_ID = f"orchestrate_{uuid.uuid4().hex[:8]}"


# ── SSE helper(与 chat.py _sse 同模式,本 router 自带一份,物理隔离)────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _orch_event(
    orch_event: str, session_id: str, payload: dict,
) -> dict:
    """orchestrate graph 事件偷渡 tick_completed(与 flow.py:96 同模式)。

    observe EventType enum 封闭(红线),orchestrate 真实语义塞进
    ``data.orch_event`` + ``data.orch_payload``,TUI 消费者按 ``orch_event``
    分类(plan Part 4)。
    """
    return {
        "event_id": str(uuid.uuid4()),
        "harness_type": "orchestrate",
        "harness_id": _ORCH_HARNESS_ID,
        "session_id": session_id,
        "tick_id": session_id,
        "event_type": "tick_completed",
        "data": {
            "status": "success",
            "response": "",
            "orch_event": orch_event,
            "orch_payload": payload,
        },
        "timestamp": _now(),
    }


def _agent_manager_shim() -> Any:
    """Build a tiny object exposing create_subagent/teardown_subagent.

    AgentWorkerNode 的 ``_has_real_lifecycle`` 守卫要求 agent_manager 同时具备
    这两个方法。``agent_manager.py`` 模块级已经提供了这两个 async 函数(P0),
    这里包一个轻量对象把它们绑成方法,既满足 hasattr 守卫又复用生产实现
    (create_subagent 接通生产 — R7: 不接 memory_event_bus)。
    """

    class _AM:
        async def create_subagent(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return await _am_create_subagent(*args, **kwargs)

        async def teardown_subagent(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return await _am_teardown_subagent(*args, **kwargs)

    return _AM()


@router.post("/orchestrate")
async def orchestrate(req: OrchestrateRequest) -> StreamingResponse:
    """Orchestrate N sub-agent roles in parallel, fan-in, and synthesize.

    Physically independent of ``/execute`` and ``/execute_parallel`` (R2):
    a separate graph builder (:func:`_build_multi_agent_graph`), separate
    request model (:class:`OrchestrateRequest`, R3), and the orchestrator
    agent is the *only* memory-eligible entity (R1 — branches are transient
    subagents with zero memory).
    """
    # ── 校验 orchestrator 是真实持久 agent ────────────────────────────
    orchestrator = _state.agents.get(req.orchestrator_agent_id)
    if not orchestrator:
        return JSONResponse(
            {"error": "Orchestrator agent not found",
             "agent_id": req.orchestrator_agent_id},
            status_code=404,
        )
    if not req.sub_agents:
        return JSONResponse(
            {"error": "sub_agents must be non-empty"}, status_code=400,
        )

    session_id = req.session_id or str(uuid.uuid4())

    # 注册 orchestrator 进 session(通信桥广播收件人非空,与 /execute 同模式)。
    _state.communication_bus.register_agent(req.orchestrator_agent_id, session_id)

    agent_manager = _agent_manager_shim()
    graph = _build_multi_agent_graph(
        orchestrator_id=req.orchestrator_agent_id,
        sub_agents_spec=req.sub_agents,
        input=req.input,
        session_id=session_id,
        agent_manager=agent_manager,
    )
    initial_state = GraphState(
        input=req.input,
        agent_id=req.orchestrator_agent_id,
        session_id=session_id,
    )

    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # observe emitter:plan Part 3.2。best-effort connect(observe 不可达不阻塞)。
    orch_emitter = ObserveEmitter(
        "orchestrate", harness_id=_ORCH_HARNESS_ID, session_id=session_id,
    )
    try:
        await orch_emitter.connect()
    except Exception as e:  # best-effort:emitter.emit 在 ws 未连时 no-op
        logger.warning("orchestrate observe connect failed: %s", e)

    async def on_node_complete(node_name: str, state: GraphState) -> None:
        # multi_agent(ParallelNode)/ fan_in / synthesizer —— 顶层图节点。
        # 分支(AgentWorkerNode)跑在 ParallelNode 克隆 state 内,不作为顶层图
        # 节点 surface;其结果经 parallel_results 汇聚后由 fan_in 节点呈现。
        try:
            await _state.communication_bus.broadcast(
                _make_notification(req.orchestrator_agent_id, session_id, node_name, state),
                session_id=session_id,
            )
        except Exception:
            pass

        if node_name == "multi_agent":
            branch_results = state.parallel_results.get("multi_agent", [])
            # ParallelNode 的 branch_data 形状:{"branch","output","status",
            # "errors","tool_results"}(不含 role/subagent_id —— 那些写在 worker
            # 的克隆 state 上,经 ParallelNode 汇聚后只保留上述字段)。role 从
            # branch 名(branch_<role>)解析,subagent_id 由闭环测试另行固化。
            def _role_of(b: dict) -> str:
                br = b.get("branch", "")
                return br[len("branch_"):] if br.startswith("branch_") else br

            await event_queue.put(_sse("node_start", {
                "node": "multi_agent", "status": "running",
                "roles": [_role_of(b) for b in branch_results],
            }))
            branches_view = [
                {
                    "branch": b.get("branch"),
                    "role": _role_of(b),
                    "output": b.get("output", ""),
                    "status": b.get("status", ""),
                }
                for b in branch_results
            ]
            await event_queue.put(_sse("node_complete", {
                "node": "multi_agent", "status": "done",
                "branch_count": len(branch_results),
                "branches": branches_view,
            }))
            await orch_emitter.emit(_orch_event(
                "node_complete", session_id,
                {"node": "multi_agent", "status": "done",
                 "branch_count": len(branch_results), "branches": branches_view},
            ))
        elif node_name == "fan_in":
            await event_queue.put(_sse("node_start", {"node": "fan_in", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "fan_in", "status": "done", "output": state.output,
            }))
            await orch_emitter.emit(_orch_event(
                "node_complete", session_id,
                {"node": "fan_in", "status": "done", "output": state.output},
            ))
        elif node_name == "synthesizer":
            await event_queue.put(_sse("node_start", {"node": "synthesizer", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "synthesizer", "status": "done", "output": state.output,
            }))
            await orch_emitter.emit(_orch_event(
                "node_complete", session_id,
                {"node": "synthesizer", "status": "done", "output": state.output},
            ))

    async def event_stream() -> AsyncGenerator[str, None]:
        # R8: 复用 ConcurrencyController(为 orchestrator 取槽)。
        await _state.concurrency_controller.acquire_agent_slot(req.orchestrator_agent_id)
        orchestrator["status"] = "running"
        running_payload = {
            "agent_id": req.orchestrator_agent_id, "status": "running",
            "orchestrate": True, "roles": [s.get("role") for s in req.sub_agents],
        }
        yield _sse("agent_status", running_payload)
        await orch_emitter.emit(_orch_event(
            "agent_status", session_id, running_payload,
        ))

        async def run_graph() -> None:
            try:
                final_state = await graph.run(
                    initial_state, on_node_complete=on_node_complete,
                )
                orchestrator["status"] = "idle"
                await event_queue.put(_sse("agent_status", {
                    "agent_id": req.orchestrator_agent_id, "status": "idle",
                }))
                await orch_emitter.emit(_orch_event(
                    "agent_status", session_id,
                    {"agent_id": req.orchestrator_agent_id, "status": "idle"},
                ))
                completion_payload = {
                    "output": final_state.output,
                    "session_id": final_state.session_id,
                    "orchestrator_agent_id": req.orchestrator_agent_id,
                    "roles": [s.get("role") for s in req.sub_agents],
                    "parallel_results": final_state.parallel_results.get("multi_agent", []),
                }
                await event_queue.put(_sse("execution_complete", completion_payload))
                await orch_emitter.emit(_orch_event(
                    "execution_complete", session_id, completion_payload,
                ))
            except Exception as exc:
                orchestrator["status"] = "idle"
                await event_queue.put(_sse("error", {"message": str(exc)}))
                await orch_emitter.emit(_orch_event(
                    "error", session_id,
                    {"status": "error", "message": str(exc)},
                ))
            finally:
                await _state.concurrency_controller.release_agent_slot(
                    req.orchestrator_agent_id,
                )
                await event_queue.put(None)

        task = asyncio.create_task(run_graph())

        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break
                yield item

            yield "data: [DONE]\n\n"
            await task
        finally:
            await orch_emitter.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _make_notification(sender_id: str, session_id: str, node_name: str, state: GraphState):
    """Build an AgentMessage for the communication-bus broadcast (best-effort)."""
    # 局部 import:避免 router 顶层强依赖 communication.message(与 chat.py 同模式)。
    from src.communication.message import AgentMessage, MessageType

    return AgentMessage(
        sender_id=sender_id,
        recipient_id=None,
        session_id=session_id,
        content=f"Node {node_name} completed: {(state.output or '')[:100]}",
        message_type=MessageType.NOTIFICATION,
    )
