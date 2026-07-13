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
) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": event_type,
        "data": data,
        "timestamp": _now(),
    }


def tick_started(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, request: str,
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tick_started", {"request": request[:500]})


def tool_call(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, tool_name: str, arguments: Dict[str, Any],
    call_id: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tool_call", {
                     "call_id": call_id or str(uuid.uuid4()),
                     "tool_name": tool_name,
                     "arguments": arguments,
                 })


def tool_result(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, call_id: str, result: Any = None, error: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"call_id": call_id}
    if error:
        payload["error"] = error
        payload["result"] = None
    else:
        payload["error"] = ""
        payload["result"] = str(result)[:500] if result else ""
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tool_result", payload)


def tick_completed(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, status: str, response: str = "",
    tool_count: int = 0, duration_ms: float = 0.0,
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "tick_completed", {
                     "status": status,
                     "response": response[:500],
                     "tool_count": tool_count,
                     "duration_ms": duration_ms,
                 })


def token_delta(
    harness_type: str, harness_id: str, session_id: str,
    tick_id: str, delta_text: str, accumulated_text: str = "",
) -> Dict[str, Any]:
    return _base(harness_type, harness_id, session_id, tick_id,
                 "token_delta", {
                     "delta_text": delta_text,
                     "accumulated_text": accumulated_text,
                 })
