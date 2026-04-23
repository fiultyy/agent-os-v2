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

    def __init__(self, event_store: CanvasEventStore) -> None:
        self._store = event_store
        # session_id -> set of WebSocket handles
        self._subscribers: Dict[str, Set[object]] = {}
        self._lock = asyncio.Lock()

    # ── Emit ────────────────────────────────────────────────────────

    async def emit(self, event: CanvasEvent) -> None:
        """Persist event and broadcast to all subscribed WS clients."""
        # 1. Persist
        self._store.append(event)

        # 2. Broadcast
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        dead_clients: List[object] = []

        async with self._lock:
            clients = self._subscribers.get(event.session_id, set())

        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                logger.warning("WS send failed for session=%s, removing client", event.session_id)
                dead_clients.append(ws)

        # Clean up disconnected clients
        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self._subscribers.get(event.session_id, set()).discard(ws)

    async def emit_many(self, events: List[CanvasEvent]) -> None:
        """Batch-emit multiple events."""
        self._store.append_many(events)
        for event in events:
            await self._broadcast_only(event)

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
                await ws_client.send_text(json.dumps(ev_dict, ensure_ascii=False))
                count += 1
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

    async def _broadcast_only(self, event: CanvasEvent) -> None:
        """Broadcast without persisting (used after batch persist)."""
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        dead_clients: List[object] = []

        async with self._lock:
            clients = self._subscribers.get(event.session_id, set())

        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self._subscribers.get(event.session_id, set()).discard(ws)
