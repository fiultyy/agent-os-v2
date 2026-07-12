"""Observe client: WebSocket client to observe-service.

Fire-and-forget design — observe-service unavailable → silent logger.warning,
never raise. Main path /execute zero-regression red-line (ADR-7).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import websockets.client as ws_client

logger = logging.getLogger(__name__)

# Observe-service WebSocket endpoint
OBSERVE_WS_URL = "ws://localhost:8002/ws/ingest"
HARNESS_TYPE = "agent-os-v2"


class ObserveClient:
    """Fire-and-forget WebSocket client to observe-service.

    Pushes turn events (tick_started/tool_call/tool_result/tick_completed)
    to observe-service for multi-harness observation (ADR-2).

    Red-line (ADR-7): observe-service unavailable → silent logger.warning,
    never raise. Main path /execute continues normally.
    """

    def __init__(self, harness_id: str = ""):
        """Initialize client.

        Args:
            harness_id: Harness instance ID (e.g., orchestrator instance ID)
        """
        self.harness_id = harness_id
        self._ws: ws_client.WebSocketClientProtocol | None = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> bool:
        """Ensure WebSocket connection, return True if connected.

        Fire-and-forget: connection failure → silent logger.warning,
        return False (caller should skip emit).
        """
        if self._ws is not None and not self._ws.closed:
            return True

        try:
            async with self._lock:
                # Double-check after acquiring lock
                if self._ws is not None and not self._ws.closed:
                    return True

                # Connect with query params (harness_type, harness_id)
                # Note: session_id is per-event, not connection-level
                url = f"{OBSERVE_WS_URL}?harness_type={HARNESS_TYPE}&harness_id={self.harness_id}&session_id=default"
                self._ws = await ws_client.connect(url)
                logger.debug("observe-service WebSocket connected")
                return True
        except Exception as e:
            logger.warning("observe-service WebSocket connection failed: %s", e)
            self._ws = None
            return False

    async def _emit_event(self, event: Dict[str, Any]) -> None:
        """Emit event to observe-service (fire-and-forget).

        Args:
            event: Event dict with keys: event_id, harness_type, harness_id,
                   session_id, tick_id, event_type, data, timestamp
        """
        try:
            if not await self._ensure_connected():
                return

            if self._ws is None or self._ws.closed:
                return

            # Wrap event in the message format expected by observe-service
            message = {"type": "event", "payload": event}
            await self._ws.send(json.dumps(message))
        except Exception as e:
            logger.warning(
                "observe-service emit failed (%s): %s",
                event.get("event_type", "?"),
                e,
                exc_info=True,
            )
            # Clear connection to force reconnect next time
            self._ws = None

    async def on_tick_started(
        self,
        session_id: str,
        tick_id: str,
        request: str,
    ) -> None:
        """Push tick_started event."""
        event = {
            "event_id": str(uuid.uuid4()),
            "harness_type": HARNESS_TYPE,
            "harness_id": self.harness_id,
            "session_id": session_id,
            "tick_id": tick_id,
            "event_type": "tick_started",
            "data": {"request": request[:500]},  # Summary by default
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._emit_event(event)

    async def on_tool_call(
        self,
        session_id: str,
        tick_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: str = "",
    ) -> None:
        """Push tool_call event."""
        event = {
            "event_id": str(uuid.uuid4()),
            "harness_type": HARNESS_TYPE,
            "harness_id": self.harness_id,
            "session_id": session_id,
            "tick_id": tick_id,
            "event_type": "tool_call",
            "data": {
                "call_id": call_id or str(uuid.uuid4()),
                "tool_name": tool_name,
                "arguments": arguments,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._emit_event(event)

    async def on_tool_result(
        self,
        session_id: str,
        tick_id: str,
        call_id: str,
        result: Any = None,
        error: str = "",
    ) -> None:
        """Push tool_result event."""
        payload: Dict[str, Any] = {"call_id": call_id}
        if error:
            payload["error"] = error
            payload["result"] = None
        else:
            payload["error"] = ""
            payload["result"] = str(result)[:500] if result else ""

        event = {
            "event_id": str(uuid.uuid4()),
            "harness_type": HARNESS_TYPE,
            "harness_id": self.harness_id,
            "session_id": session_id,
            "tick_id": tick_id,
            "event_type": "tool_result",
            "data": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._emit_event(event)

    async def on_tick_completed(
        self,
        session_id: str,
        tick_id: str,
        status: str,  # "success" | "error"
        response: str = "",
        tool_count: int = 0,
        duration_ms: float = 0.0,
    ) -> None:
        """Push tick_completed event."""
        event = {
            "event_id": str(uuid.uuid4()),
            "harness_type": HARNESS_TYPE,
            "harness_id": self.harness_id,
            "session_id": session_id,
            "tick_id": tick_id,
            "event_type": "tick_completed",
            "data": {
                "status": status,
                "response": response[:500],
                "tool_count": tool_count,
                "duration_ms": duration_ms,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._emit_event(event)

    async def close(self) -> None:
        """Close WebSocket connection (fire-and-forget)."""
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        finally:
            self._ws = None
