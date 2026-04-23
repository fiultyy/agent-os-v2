"""P0-4: TickTracker — tracks tick lifecycle across the LLM call layer.

Provides integration points for the existing LLM call layer to emit
canvas events (tick_started, token_delta, tool_call, tick_completed)
**without modifying existing code**.  The existing code calls into
these hooks via the emitter that the tracker wraps.

Usage (from existing LLM layer, zero modification to that layer):

    tracker = TickTracker(emitter)

    # Before LLM call
    tick = tracker.start_tick(session_id, branch_id, request)

    # During streaming
    tracker.record_token(tick, token)

    # Tool execution
    tracker.record_tool_call(tick, tool_name, args)
    tracker.record_tool_result(tick, call_id, result)

    # After LLM call
    tracker.complete_tick(tick, response)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.canvas.events import (
    TickStartedEvent,
    TokenDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    TickCompletedEvent,
)
from src.canvas.tick import Tick, TickStatus

if TYPE_CHECKING:
    from src.canvas.emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


class TickTracker:
    """Tracks tick lifecycle and emits canvas events.

    This class is the bridge between the existing LLM call layer and
    the canvas event system.  It does **not** modify any existing code;
    instead, it provides methods that the orchestrator layer can call
    at the appropriate lifecycle points.

    Integration points (call these from the LLM call layer):

    1. ``start_tick()``    — before sending request to LLM
    2. ``record_token()``  — during streaming response
    3. ``record_tool_call()``  — before executing a tool
    4. ``record_tool_result()`` — after tool returns
    5. ``complete_tick()`` — after full response received
    6. ``fail_tick()``     — on error
    """

    def __init__(
        self,
        emitter: "SessionEventEmitter",
        lod: int = 2,
    ) -> None:
        self._emitter = emitter
        self._lod = lod
        # tick_id -> Tick (in-flight tracking)
        self._active_ticks: Dict[str, Tick] = {}

    # ── Lifecycle: start ────────────────────────────────────────────

    def start_tick(
        self,
        session_id: str,
        branch_id: str,
        request: str,
        parent_tick_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tick:
        """Create a new tick and emit tick_started event.

        This should be called right before the LLM request is sent.

        Args:
            session_id: Canvas session ID.
            branch_id: Branch ID (default "main").
            request: The full request text sent to the LLM.
            parent_tick_id: Previous tick in the branch chain.
            metadata: Optional extra metadata.

        Returns:
            The created Tick object (also tracked internally).
        """
        tick = Tick(
            branch_id=branch_id,
            parent_tick_id=parent_tick_id,
            request=request,
            status=TickStatus.RUNNING,
            metadata=metadata or {},
        )
        self._active_ticks[tick.tick_id] = tick

        event = TickStartedEvent.create(
            session_id=session_id,
            branch_id=branch_id,
            tick_id=tick.tick_id,
            request=request,
            lod=self._lod,
        )
        # Schedule async emit — callers should await if needed
        self._emitter.emit(event)

        logger.debug(
            "Tick started: %s session=%s branch=%s",
            tick.tick_id, session_id, branch_id,
        )
        return tick

    # ── Lifecycle: streaming ────────────────────────────────────────

    def record_token(
        self,
        tick: Tick,
        token: str,
        index: int = 0,
        session_id: str = "",
        branch_id: str = "",
    ) -> None:
        """Record a streaming token delta.

        This should be called during LLM streaming response, once per token.
        """
        event = TokenDeltaEvent.create(
            session_id=session_id or tick.branch_id,
            branch_id=branch_id or tick.branch_id,
            tick_id=tick.tick_id,
            token=token,
            index=index,
        )
        self._emitter.emit(event)
        tick.response += token

    # ── Lifecycle: tool calls ───────────────────────────────────────

    def record_tool_call(
        self,
        tick: Tick,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: str = "",
        session_id: str = "",
        branch_id: str = "",
    ) -> str:
        """Record a tool invocation within a tick.

        Returns the call_id for later matching with record_tool_result.
        """
        from src.canvas.tick import ToolCall

        cid = call_id or str(uuid.uuid4())
        tc = ToolCall(
            call_id=cid,
            tool_name=tool_name,
            arguments=arguments,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        tick.tool_calls.append(tc)

        event = ToolCallEvent.create(
            session_id=session_id or tick.branch_id,
            branch_id=branch_id or tick.branch_id,
            tick_id=tick.tick_id,
            tool_name=tool_name,
            arguments=arguments,
            call_id=cid,
            lod=self._lod,
        )
        self._emitter.emit(event)
        return cid

    def record_tool_result(
        self,
        tick: Tick,
        call_id: str,
        result: Any = None,
        error: str = "",
        session_id: str = "",
        branch_id: str = "",
    ) -> None:
        """Record the result of a tool call."""
        # Update the ToolCall in the tick
        for tc in tick.tool_calls:
            if tc.call_id == call_id:
                tc.result = result
                tc.error = error
                tc.completed_at = datetime.now(timezone.utc).isoformat()
                break

        event = ToolResultEvent.create(
            session_id=session_id or tick.branch_id,
            branch_id=branch_id or tick.branch_id,
            tick_id=tick.tick_id,
            call_id=call_id,
            result=result,
            error=error,
            lod=self._lod,
        )
        self._emitter.emit(event)

    # ── Lifecycle: complete / fail ──────────────────────────────────

    def complete_tick(
        self,
        tick: Tick,
        response: str = "",
        session_id: str = "",
        branch_id: str = "",
    ) -> None:
        """Mark a tick as completed and emit tick_completed event."""
        tick.status = TickStatus.COMPLETED
        tick.completed_at = datetime.now(timezone.utc).isoformat()
        if response:
            tick.response = response

        event = TickCompletedEvent.create(
            session_id=session_id or tick.branch_id,
            branch_id=branch_id or tick.branch_id,
            tick_id=tick.tick_id,
            status="completed",
            response=tick.response,
            tool_count=tick.tool_count,
            duration_ms=tick.duration_ms or 0.0,
            lod=self._lod,
        )
        self._emitter.emit(event)
        self._active_ticks.pop(tick.tick_id, None)

        logger.debug("Tick completed: %s (%d tools, %.0fms)",
                      tick.tick_id, tick.tool_count, tick.duration_ms or 0)

    def fail_tick(
        self,
        tick: Tick,
        error: str,
        session_id: str = "",
        branch_id: str = "",
    ) -> None:
        """Mark a tick as failed and emit tick_completed event with error."""
        tick.status = TickStatus.FAILED
        tick.error = error
        tick.completed_at = datetime.now(timezone.utc).isoformat()

        event = TickCompletedEvent.create(
            session_id=session_id or tick.branch_id,
            branch_id=branch_id or tick.branch_id,
            tick_id=tick.tick_id,
            status="failed",
            response=error,
            duration_ms=tick.duration_ms or 0.0,
            lod=self._lod,
        )
        self._emitter.emit(event)
        self._active_ticks.pop(tick.tick_id, None)

        logger.warning("Tick failed: %s — %s", tick.tick_id, error[:200])

    # ── Introspection ───────────────────────────────────────────────

    def get_active_tick(self, tick_id: str) -> Optional[Tick]:
        """Return an in-flight tick by ID."""
        return self._active_ticks.get(tick_id)

    @property
    def active_tick_count(self) -> int:
        return len(self._active_ticks)
