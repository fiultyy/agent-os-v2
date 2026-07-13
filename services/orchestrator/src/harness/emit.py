"""Observe ingest WS client for the harness layer.

Connects to observe-service /ws/ingest and ships ObserveEvent dicts as
{type:"event", payload:<dict>}. One persistent connection per
(harness_type, harness_id, session_id). Fire-and-forget: if observe is
unreachable the event is logged and dropped — never raises (the harness turn
keeps running).

Per spec section 4 / ADR-4: orchestrator is the ONLY harness client; events
flow harness → orchestrator (connect+map) → observe /ws/ingest → TUI.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict
from urllib.parse import quote

import websockets.client as ws_client

logger = logging.getLogger(__name__)

OBSERVE_INGEST_URL = "ws://localhost:8002/ws/ingest"


class ObserveEmitter:
    """Persistent WS client to observe /ws/ingest for one session registration.

    Call connect() once (in the background loop), then emit(event_dict) for
    each mapped event. close() on session teardown.
    """

    def __init__(
        self,
        harness_type: str,
        harness_id: str,
        session_id: str,
        observe_url: str = OBSERVE_INGEST_URL,
    ):
        self.harness_type = harness_type
        self.harness_id = harness_id
        self.session_id = session_id
        self.observe_url = observe_url
        self._ws = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Open the ingest WS. Returns True on success, False on failure."""
        try:
            url = (
                f"{self.observe_url}"
                f"?harness_type={quote(self.harness_type, safe='')}"
                f"&session_id={quote(self.session_id, safe='')}"
                f"&harness_id={quote(self.harness_id, safe='')}"
            )
            self._ws = await ws_client.connect(url)
            logger.info(
                "observe ingest connected: %s/%s (harness_id=%s)",
                self.harness_type, self.session_id, self.harness_id,
            )
            return True
        except Exception as e:
            logger.warning("observe ingest connect failed: %s", e)
            self._ws = None
            return False

    async def emit(self, event: Dict[str, Any]) -> None:
        """Ship one ObserveEvent dict. Fire-and-forget."""
        if self._ws is None:
            return
        try:
            async with self._lock:
                await self._ws.send(
                    json.dumps({"type": "event", "payload": event})
                )
        except Exception as e:
            logger.warning(
                "observe ingest emit failed (%s): %s",
                event.get("event_type", "?"), e,
            )

    async def close(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        except Exception:
            pass
        finally:
            self._ws = None
