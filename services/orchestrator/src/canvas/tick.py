"""P0-2: Tick — atomic unit of LLM interaction.

1 LLM request + 1 LLM response = 1 Tick.
A tick may contain zero or more tool calls.

The tick is the fundamental timeline marker on the canvas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TickStatus(str, Enum):
    """Lifecycle states for a tick."""
    PENDING = "pending"        # created, not yet started
    RUNNING = "running"        # LLM request in-flight
    COMPLETED = "completed"    # successfully finished
    FAILED = "failed"          # ended with error
    CANCELLED = "cancelled"    # user or system cancelled


@dataclass
class ToolCall:
    """A single tool invocation within a tick."""
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_call_id": self.call_id,
            "tool_name": self.tool_name,
            "args": self.arguments,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class Tick:
    """Atomic unit of LLM interaction.

    A tick captures:
    - The request sent to the LLM.
    - The response received (or error).
    - Any tool calls made during the request.
    - Timing information.

    Ticks are linked in a timeline via ``parent_tick_id`` (branch chain).
    """
    tick_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str = ""
    parent_tick_id: Optional[str] = None
    request: str = ""
    response: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    status: TickStatus = TickStatus.PENDING
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        """Calculate duration in ms if both timestamps are present."""
        if self.completed_at and self.status in (TickStatus.COMPLETED, TickStatus.FAILED):
            try:
                start = datetime.fromisoformat(self.created_at)
                end = datetime.fromisoformat(self.completed_at)
                return (end - start).total_seconds() * 1000
            except (ValueError, TypeError):
                return None
        return None

    @property
    def tool_count(self) -> int:
        return len(self.tool_calls)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "branch_id": self.branch_id,
            "parent_tick_id": self.parent_tick_id,
            "request": self.request,
            "response": self.response,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metadata": self.metadata,
            "duration_ms": self.duration_ms,
            "tool_count": self.tool_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Tick":
        tool_calls_data = d.get("tool_calls", [])
        tool_calls = [ToolCall(**tc) for tc in tool_calls_data]
        status_val = d.get("status", "pending")
        d.get("duration_ms")
        d.get("tool_count")
        return cls(
            tool_calls=tool_calls,
            status=TickStatus(status_val),
            **{k: v for k, v in d.items() if k in cls.__dataclass_fields__},
        )
