"""Observe-Service Event Protocol: Unified turn event schema.

泛化 canvas.events，去 agent-os-v2 耦合（GraphState），支持多 harness 接入。
语言无关，供 TS/JS gateway 自实现（见 protocol/SCHEMA.md）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict


# ── Event Types ─────────────────────────────────────────────────────

class EventType(str, Enum):
    """Turn event types."""
    TICK_STARTED = "tick_started"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TICK_COMPLETED = "tick_completed"
    BRANCH_CREATED = "branch_created"  # Optional: agent-os-v2 specific
    BRANCH_MERGED = "branch_merged"    # Optional: agent-os-v2 specific
    TOKEN_DELTA = "token_delta"


# ── Base Event ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObserveEvent:
    """Unified turn event for multi-harness observation.

    Immutable (frozen=True) — events are facts, never mutated.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    harness_type: str = ""  # agent-os-v2, claude-code, openclaw, mock-*
    harness_id: str = ""    # Harness instance ID (tmux session, openclaw session)
    session_id: str = ""    # Composite key: (harness_type, session_id)
    tick_id: str = ""
    event_type: EventType = EventType.TICK_STARTED
    data: Dict[str, Any] = field(default_factory=dict)
    # ADR-1: semantic agent_id (e.g. "native"/"main"). Empty for legacy events.
    # Lets observe tell which agent a turn belongs to — the basis of A2A mesh
    # traceability. A consumed agent's events carry the TARGET agent_id, not the
    # caller's (set by orchestrator assemble_capabilities).
    agent_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict (for JSON / WS transmission)."""
        return {
            "event_id": self.event_id,
            "harness_type": self.harness_type,
            "harness_id": self.harness_id,
            "session_id": self.session_id,
            "tick_id": self.tick_id,
            "event_type": self.event_type.value,
            "data": self.data,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ObserveEvent":
        """Deserialize from dict."""
        copy = dict(d)
        # Convert event_type string back to Enum
        if "event_type" in copy and isinstance(copy["event_type"], str):
            copy["event_type"] = EventType(copy["event_type"])
        return cls(**{k: v for k, v in copy.items() if k in cls.__dataclass_fields__})


# ── Concrete Event Constructors ────────────────────────────────────────

def tick_started(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    request: str,
) -> ObserveEvent:
    """Create tick_started event."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id=tick_id,
        event_type=EventType.TICK_STARTED,
        data={"request": request[:500]},  # Summary by default
    )


def tool_call(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    call_id: str = "",
) -> ObserveEvent:
    """Create tool_call event."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id=tick_id,
        event_type=EventType.TOOL_CALL,
        data={
            "call_id": call_id or str(uuid.uuid4()),
            "tool_name": tool_name,
            "arguments": arguments,
        },
    )


def tool_result(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    call_id: str,
    result: Any = None,
    error: str = "",
) -> ObserveEvent:
    """Create tool_result event."""
    payload: Dict[str, Any] = {"call_id": call_id}
    if error:
        payload["error"] = error
        payload["result"] = None
    else:
        payload["error"] = ""
        payload["result"] = str(result)[:500] if result else ""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id=tick_id,
        event_type=EventType.TOOL_RESULT,
        data=payload,
    )


def tick_completed(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    status: str,  # "success" | "error"
    response: str = "",
    tool_count: int = 0,
    duration_ms: float = 0.0,
) -> ObserveEvent:
    """Create tick_completed event."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id=tick_id,
        event_type=EventType.TICK_COMPLETED,
        data={
            "status": status,
            "response": response[:500],
            "tool_count": tool_count,
            "duration_ms": duration_ms,
        },
    )


def tick_delta(
    harness_type: str,
    harness_id: str,
    session_id: str,
    tick_id: str,
    delta_text: str,
    accumulated_text: str = "",
) -> ObserveEvent:
    """Create token_delta event (streaming token deltas)."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id=tick_id,
        event_type=EventType.TOKEN_DELTA,
        data={
            "delta_text": delta_text,
            "accumulated_text": accumulated_text,
        },
    )


def branch_created(
    harness_type: str,
    harness_id: str,
    session_id: str,
    branch_id: str,
    parent_branch_id: str,
    fork_tick_id: str,
) -> ObserveEvent:
    """Create branch_created event (agent-os-v2 specific)."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id="",  # Not tied to a specific tick
        event_type=EventType.BRANCH_CREATED,
        data={
            "branch_id": branch_id,
            "parent_branch_id": parent_branch_id,
            "fork_tick_id": fork_tick_id,
        },
    )


def branch_merged(
    harness_type: str,
    harness_id: str,
    session_id: str,
    branch_id: str,
    target_branch_id: str,
    merge_tick_id: str = "",
) -> ObserveEvent:
    """Create branch_merged event (agent-os-v2 specific)."""
    return ObserveEvent(
        harness_type=harness_type,
        harness_id=harness_id,
        session_id=session_id,
        tick_id="",
        event_type=EventType.BRANCH_MERGED,
        data={
            "branch_id": branch_id,
            "target_branch_id": target_branch_id,
            "merge_tick_id": merge_tick_id,
        },
    )
