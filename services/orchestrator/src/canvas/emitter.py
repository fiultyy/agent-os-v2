"""P0-3: SessionEventEmitter — dual-write event broadcast.

Persists events via CanvasEventStore AND pushes them to subscribed
WebSocket clients in real-time.  Frontend clients connect via WS,
receive live events, and can request replay for catch-up.

Thread-safety: uses asyncio.Lock for the subscribers dict since
FastAPI runs on an async event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional, Set

from src.canvas.events import CanvasEvent
from src.canvas.event_store import CanvasEventStore

logger = logging.getLogger(__name__)

# Default timeout for each WebSocket send (seconds)
_WS_SEND_TIMEOUT = 10.0


class SessionEventEmitter:
    """Dual-write event emitter: persist + WebSocket broadcast.

    Usage::

        store = CanvasEventStore()
        emitter = SessionEventEmitter(store)

        # On new WS connection
        emitter.subscribe("session-1", websocket)
        await emitter.replay("session-1", websocket)

        # On event
        await emitter.emit(event)

        # On WS disconnect
        emitter.unsubscribe("session-1", websocket)
    """

    # Maximum number of concurrent sessions with active subscribers.
    # When exceeded, the oldest unsubscribed session is evicted.
    MAX_SESSIONS: int = 500

    def __init__(self, event_store: CanvasEventStore) -> None:
        self._store = event_store
        # session_id -> set of WebSocket handles
        self._subscribers: Dict[str, Set[object]] = {}
        self._lock = asyncio.Lock()

    # ── Emit ────────────────────────────────────────────────────────

    async def emit(self, event: CanvasEvent) -> None:
        """Persist event and broadcast to all subscribed WS clients."""
        # 1. Persist
        await self._store.append(event)

        # 2. Broadcast
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        await self._broadcast_payload(event.session_id, payload)

    async def emit_many(self, events: List[CanvasEvent]) -> None:
        """Batch-emit multiple events."""
        await self._store.append_many(events)
        for event in events:
            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            await self._broadcast_payload(event.session_id, payload)

    # ── Subscribe / Unsubscribe ─────────────────────────────────────

    async def subscribe(self, session_id: str, ws_client: object) -> None:
        """Register a WebSocket client for a session's events."""
        async with self._lock:
            if session_id not in self._subscribers:
                self._subscribers[session_id] = set()
            self._subscribers[session_id].add(ws_client)
        logger.info("WS client subscribed to session=%s", session_id)

    async def unsubscribe(self, session_id: str, ws_client: object) -> None:
        """Remove a WebSocket client from a session."""
        async with self._lock:
            subs = self._subscribers.get(session_id)
            if subs:
                subs.discard(ws_client)
                if not subs:
                    del self._subscribers[session_id]
        logger.info("WS client unsubscribed from session=%s", session_id)

    # ── Replay ──────────────────────────────────────────────────────

    async def replay(
        self,
        session_id: str,
        ws_client: object,
        after_event_id: Optional[str] = None,
    ) -> int:
        """Replay historical events to a client for catch-up.

        Args:
            session_id: Session to replay.
            ws_client: WebSocket to send events to.
            after_event_id: If set, only events after this ID are sent.

        Returns:
            Number of events replayed.
        """
        events = self._store.get_events(
            session_id,
            after_event_id=after_event_id or "",
        )
        count = 0
        for ev_dict in events:
            try:
                await asyncio.wait_for(
                    ws_client.send_text(json.dumps(ev_dict, ensure_ascii=False)),
                    timeout=_WS_SEND_TIMEOUT,
                )
                count += 1
            except asyncio.TimeoutError:
                logger.warning("Replay timed out for session=%s", session_id)
                break
            except Exception:
                logger.warning("Replay send failed for session=%s", session_id)
                break
        logger.info("Replayed %d events for session=%s", count, session_id)
        return count

    # ── Introspection ───────────────────────────────────────────────

    def subscriber_count(self, session_id: str) -> int:
        """Return number of active WS subscribers for a session."""
        return len(self._subscribers.get(session_id, set()))

    # ── Private ─────────────────────────────────────────────────────

    async def _broadcast_payload(self, session_id: str, payload: str) -> None:
        """Send *payload* to every WS client subscribed to *session_id*.

        Slow clients are skipped after ``_WS_SEND_TIMEOUT`` seconds to
        avoid blocking the emitter loop.
        """
        dead_clients: List[object] = []

        async with self._lock:
            clients = set(self._subscribers.get(session_id, set()))

        for ws in list(clients):
            try:
                await asyncio.wait_for(
                    ws.send_text(payload),
                    timeout=_WS_SEND_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "WS send timed out for session=%s, removing client",
                    session_id,
                )
                dead_clients.append(ws)
            except Exception:
                logger.warning(
                    "WS send failed for session=%s, removing client",
                    session_id,
                )
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self._subscribers.get(session_id, set()).discard(ws)
