"""P0-1: Event Sourcing protocol — immutable canvas events.

Every mutation or observation in the canvas is recorded as an event.
Events are the single source of truth; the frontend replays them to
reconstruct state (Event Sourcing pattern).

LOD (Level of Detail):
  1 — Tool name only (lightweight).
  2 — Summary / key fields (default).
  3 — Full payload (dev-debug).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


# ── LOD enum ───────────────────────────────────────────────────────

class LOD(int, Enum):
    """Level-of-detail for event payloads."""
    TOOL_NAME = 1
    SUMMARY = 2
    FULL = 3


# ── Base event ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanvasEvent:
    """Base class for all canvas events.

    Immutable (frozen=True) — events are facts, never mutated.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    branch_id: str = ""
    tick_id: str = ""
    event_type: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    lod: int = LOD.SUMMARY
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (for JSON / SSE / WS transmission)."""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "branch_id": self.branch_id,
            "tick_id": self.tick_id,
            "type": self.event_type,
            "data": self.data,
            "lod": self.lod,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CanvasEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Concrete event types ───────────────────────────────────────────

@dataclass(frozen=True)
class TickStartedEvent(CanvasEvent):
    """Fired when a new LLM request begins."""
    event_type: str = "tick.started"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        request: str,
        lod: int = LOD.SUMMARY,
    ) -> "TickStartedEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={"request": request[:500] if lod == LOD.SUMMARY else request},
            lod=lod,
        )


@dataclass(frozen=True)
class TokenDeltaEvent(CanvasEvent):
    """Streaming token delta — pushed during LLM streaming."""
    event_type: str = "token.delta"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        token: str,
        index: int = 0,
    ) -> "TokenDeltaEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={"token": token, "index": index},
            lod=LOD.FULL,
        )


@dataclass(frozen=True)
class ToolCallEvent(CanvasEvent):
    """A tool invocation within a tick."""
    event_type: str = "tool.call"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: str = "",
        lod: int = LOD.SUMMARY,
    ) -> "ToolCallEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={
                "tool_name": tool_name,
                "arguments": arguments if lod == LOD.FULL else {},
                "call_id": call_id or str(uuid.uuid4()),
            },
            lod=lod,
        )


@dataclass(frozen=True)
class ToolResultEvent(CanvasEvent):
    """Result returned by a tool execution."""
    event_type: str = "tool.result"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        call_id: str,
        result: Any,
        error: str = "",
        lod: int = LOD.SUMMARY,
    ) -> "ToolResultEvent":
        payload: Dict[str, Any] = {"call_id": call_id}
        if error:
            payload["error"] = error
        elif lod == LOD.FULL:
            payload["result"] = result
        else:
            payload["result"] = str(result)[:200]
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data=payload,
            lod=lod,
        )


@dataclass(frozen=True)
class TickCompletedEvent(CanvasEvent):
    """Fired when a tick finishes (success or error)."""
    event_type: str = "tick.completed"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        status: str,
        response: str = "",
        tool_count: int = 0,
        duration_ms: float = 0.0,
        lod: int = LOD.SUMMARY,
    ) -> "TickCompletedEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={
                "status": status,
                "response": response[:500] if lod == LOD.SUMMARY else response,
                "tool_count": tool_count,
                "duration_ms": duration_ms,
            },
            lod=lod,
        )


@dataclass(frozen=True)
class BranchCreatedEvent(CanvasEvent):
    """A branch was created from a fork point."""
    event_type: str = "branch.created"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        parent_branch_id: str,
        fork_tick_id: str,
    ) -> "BranchCreatedEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            data={
                "parent_branch_id": parent_branch_id,
                "fork_tick_id": fork_tick_id,
            },
        )


@dataclass(frozen=True)
class BranchMergedEvent(CanvasEvent):
    """A branch was merged back into its parent."""
    event_type: str = "branch.merged"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        target_branch_id: str,
        merge_tick_id: str = "",
    ) -> "BranchMergedEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            data={
                "target_branch_id": target_branch_id,
                "merge_tick_id": merge_tick_id,
            },
        )


@dataclass(frozen=True)
class ScoringSignalEvent(CanvasEvent):
    """Scoring signal emitted by the sideline agent system."""
    event_type: str = "scoring.signal"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        signal_type: str,
        score: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ScoringSignalEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={
                "signal_type": signal_type,
                "score": score,
                "metadata": metadata or {},
            },
        )


@dataclass(frozen=True)
class CommitteeVoteEvent(CanvasEvent):
    """A vote from the Profile Generation Committee."""
    event_type: str = "committee.vote"

    @classmethod
    def create(
        cls,
        session_id: str,
        branch_id: str,
        tick_id: str,
        voter: str,
        vote: str,
        rationale: str = "",
    ) -> "CommitteeVoteEvent":
        return cls(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick_id,
            data={
                "voter": voter,
                "vote": vote,
                "rationale": rationale,
            },
        )
