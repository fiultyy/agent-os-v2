"""ObserveEvent constructors for the harness layer.

Mirrors services/observe/src/events.py schema (event_id/harness_type/harness_id/
session_id/tick_id/event_type/data/timestamp) but is self-contained so the
orchestrator does not cross-import the observe service. These build plain dicts
that are shipped verbatim to observe /ws/ingest as {type:"event", payload:<dict>}.

Ponytail: dict builders, no dataclass — the wire format is the contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    event_type: str,
    data: Dict[str, Any],
    agent_id: str = "",
) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": event_type,
        "data": data,
        "agent_id": agent_id,
        "timestamp": _now(),
    }


def tick_started(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, request: str, *, agent_id: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tick_started", {"request": request[:500]}, agent_id=agent_id)


def tool_call(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, tool_name: str, arguments: Dict[str, Any],
    call_id: str = "", *, agent_id: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tool_call", {
                     "call_id": call_id or str(uuid.uuid4()),
                     "tool_name": tool_name,
                     "arguments": arguments,
                 }, agent_id=agent_id)


def tool_result(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, call_id: str, result: Any = None, error: str = "",
    *, agent_id: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"call_id": call_id}
    if error:
        payload["error"] = error
        payload["result"] = None
    else:
        payload["error"] = ""
        payload["result"] = str(result)[:500] if result else ""
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tool_result", payload, agent_id=agent_id)


def tick_completed(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, status: str, response: str = "",
    tool_count: int = 0, duration_ms: float = 0.0,
    *, agent_id: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tick_completed", {
                     "status": status,
                     "response": response[:500],
                     "tool_count": tool_count,
                     "duration_ms": duration_ms,
                 }, agent_id=agent_id)


def token_delta(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, delta_text: str, accumulated_text: str = "",
    *, agent_id: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "token_delta", {
                     "delta_text": delta_text,
                     "accumulated_text": accumulated_text,
                 }, agent_id=agent_id)


def branch_created(
    harness_type: str, harness_id: str, session_id: str,
    branch_id: str, parent_branch_id: str, fork_tick_id: str = "",
    *, agent_id: str = "",
) -> Dict[str, Any]:
    """F3(ADR-S5):fork 时 emit。session_id=child(新 fork),parent_branch_id=source。

    observe ws_ingest 据此回填 child session 的 parent_session_id → fork 树可观测。
    agent_id 透传(D 的字段),让 observe 知道是谁的 fork。

    顶层 ``parent_session_id`` 键必须显式写:_base 不含此键,而 observe
    ``ObserveEvent.from_dict`` 读顶层字段(非 data)回填 session 行。漏写则
    ws_ingest 的 update_parent_session_id 永不触发(parent 恒空串)。
    """
    ev = _base(harness_type, harness_id, session_id, "",
               "branch_created", {
                   "branch_id": branch_id,
                   "parent_branch_id": parent_branch_id,
                   "fork_tick_id": fork_tick_id,
               }, agent_id=agent_id)
    ev["parent_session_id"] = parent_branch_id  # 顶层(observe from_dict 读此)
    return ev
