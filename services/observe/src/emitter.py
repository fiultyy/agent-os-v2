"""Event Emitter: Dual-write (persist + WebSocket broadcast).

参考 canvas/emitter.py 的双写模式，适配 observe-service 的复合键 session。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Optional, Set

from src.event_store import EventStore
from src.events import ObserveEvent

logger = logging.getLogger(__name__)

_WS_SEND_TIMEOUT = 10.0


class EventEmitter:
    """Dual-write event emitter: persist + WebSocket broadcast.

    Usage::

        store = EventStore()
        emitter = EventEmitter(store)

        # WS subscribe
        await emitter.subscribe("agent-os-v2", "session-1", websocket)
        await emitter.replay("agent-os-v2", "session-1", websocket)

        # On event
        await emitter.emit(event)

        # On disconnect
        await emitter.unsubscribe("agent-os-v2", "session-1", websocket)
    """

    MAX_SESSIONS: int = 500

    def __init__(self, event_store: EventStore) -> None:
        self._store = event_store
        # Composite key (harness_type, session_id) -> set of WebSocket
        self._subscribers: Dict[tuple, Set[object]] = {}
        self._lock = asyncio.Lock()

    # ── Emit ────────────────────────────────────────────────────────

    async def emit(self, event: ObserveEvent) -> None:
        """Persist event and broadcast to subscribed WS clients."""
        # 1. Persist
        await self._store.append(event)

        # 2. Broadcast
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        key = (event.harness_type, event.session_id)
        await self._broadcast_payload(key, payload)

    # ── Subscribe / Unsubscribe ───────────────────────────────────────

    async def subscribe(
        self,
        harness_type: str,
        session_id: str,
        ws_client: object,
    ) -> None:
        """Register a WebSocket client for a session."""
        key = (harness_type, session_id)
        async with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = set()
            self._subscribers[key].add(ws_client)
        logger.info(f"WS subscribed: {harness_type}/{session_id}")

    async def unsubscribe(
        self,
        harness_type: str,
        session_id: str,
        ws_client: object,
    ) -> None:
        """Remove a WebSocket client."""
        key = (harness_type, session_id)
        async with self._lock:
            subs = self._subscribers.get(key)
            if subs:
                subs.discard(ws_client)
                if not subs:
                    del self._subscribers[key]
        logger.info(f"WS unsubscribed: {harness_type}/{session_id}")

    # ── Replay ──────────────────────────────────────────────────────

    async def replay(
        self,
        harness_type: str,
        session_id: str,
        ws_client: object,
        after_event_id: Optional[str] = None,
    ) -> int:
        """Replay historical events for catch-up."""
        events = self._store.get_events(
            harness_type,
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
                logger.warning(f"Replay timeout: {harness_type}/{session_id}")
                break
            except Exception:
                logger.warning(f"Replay send failed: {harness_type}/{session_id}")
                break
        logger.info(f"Replayed {count} events for {harness_type}/{session_id}")
        return count

    # ── Introspection ────────────────────────────────────────────────

    def subscriber_count(self, harness_type: str, session_id: str) -> int:
        """Return number of active WS subscribers for a session."""
        key = (harness_type, session_id)
        return len(self._subscribers.get(key, set()))

    # ── Private ─────────────────────────────────────────────────────

    async def _broadcast_payload(self, key: tuple, payload: str) -> None:
        """Send payload to every WS client subscribed to the session."""
        dead_clients: List[object] = []

        async with self._lock:
            clients = set(self._subscribers.get(key, set()))

        for ws in list(clients):
            try:
                await asyncio.wait_for(
                    ws.send_text(payload),
                    timeout=_WS_SEND_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"WS send timeout, removing client")
                dead_clients.append(ws)
            except Exception:
                logger.warning(f"WS send failed, removing client")
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self._subscribers.get(key, set()).discard(ws)
