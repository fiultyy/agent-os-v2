"""OpenClaw Gateway: Connect to OpenClaw gateway (port 18789) and bridge to observe-service.

ACP (Agent Client Protocol) long connection + sessions.messages.subscribe.
Events mapping: ChatEvent → ObserveEvent (tick_started, tool_call, tool_result, tick_completed).
Interactive routing: observe /send → openclaw chat.send.
Real session management: sessionKey ↔ (harness_type="openclaw", session_id).

Protocol: GatewayFrame (RequestFrame/ResponseFrame/EventFrame) over WebSocket.
Version: 4
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import websockets.client as ws_client

# Import observe event types from parent module
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from events import (
    ObserveEvent,
    tick_completed,
    tick_started,
    tool_call,
    tool_result,
    EventType,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Protocol Constants ────────────────────────────────────────────────

PROTOCOL_VERSION = 4
GATEWAY_DEFAULT_PORT = 18789
GATEWAY_DEFAULT_HOST = "localhost"

# Frame types
FRAME_REQ = "req"
FRAME_RES = "res"
FRAME_EVENT = "event"


# ── Protocol Frame Serialization ────────────────────────────────────────

def serialize_request_frame(
    req_id: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Serialize a request frame to JSON."""
    frame = {
        "type": FRAME_REQ,
        "id": req_id,
        "method": method,
        "params": params or {},
    }
    return json.dumps(frame)


def parse_event_frame(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse an event frame from gateway.

    Returns: parsed payload or None if not an event frame.
    """
    if data.get("type") != FRAME_EVENT:
        return None
    return data.get("payload")


# ── Event Mapping: OpenClaw ChatEvent → ObserveEvent ───────────────────

def map_chat_event_to_observe(
    chat_event: Dict[str, Any],
    harness_id: str,
    session_id: str,
) -> Optional[ObserveEvent]:
    """Map OpenClaw ChatEvent to ObserveEvent.

    ChatEvent types:
    - delta: {state: "delta", deltaText, message, usage} → defer (token_delta)
    - final: {state: "final", message, usage, stopReason} → tick_completed
    - aborted: {state: "aborted", message, stopReason} → tick_completed (status=error)
    - error: {state: "error", message, errorMessage, errorKind, usage, stopReason} → tick_completed (status=error)

    Agent tool events (separate event type):
    - agent tool start → tool_call
    - agent tool result → tool_result
    """
    state = chat_event.get("state")
    run_id = chat_event.get("runId", "")
    session_key = chat_event.get("sessionKey", session_id)
    agent_id = chat_event.get("agentId", "")

    # Use run_id as tick_id for this turn
    tick_id = run_id or f"tick_{uuid.uuid4().hex[:8]}"

    # Map chat final → tick_completed
    if state == "final":
        message = chat_event.get("message", {})
        usage = chat_event.get("usage", {})
        stop_reason = chat_event.get("stopReason", "")

        # Extract response summary from message
        response = ""
        if isinstance(message, dict):
            content_blocks = message.get("content", [])
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        response += block.get("text", "")

        return tick_completed(
            harness_type="openclaw",
            harness_id=harness_id,
            session_id=session_key,
            tick_id=tick_id,
            status="success",
            response=response[:500],
            tool_count=0,  # Tool count tracked separately via agent events
            duration_ms=0.0,  # Duration not provided in ChatEvent
        )

    # Map chat aborted → tick_completed (error)
    elif state == "aborted":
        return tick_completed(
            harness_type="openclaw",
            harness_id=harness_id,
            session_id=session_key,
            tick_id=tick_id,
            status="error",
            response="Turn aborted by user or coordinator",
            tool_count=0,
            duration_ms=0.0,
        )

    # Map chat error → tick_completed (error)
    elif state == "error":
        error_message = chat_event.get("errorMessage", "Unknown error")
        error_kind = chat_event.get("errorKind", "unknown")

        return tick_completed(
            harness_type="openclaw",
            harness_id=harness_id,
            session_id=session_key,
            tick_id=tick_id,
            status="error",
            response=f"Turn failed: {error_kind} - {error_message}",
            tool_count=0,
            duration_ms=0.0,
        )

    # Map chat delta → defer (token_delta not implemented in P2)
    elif state == "delta":
        # Token streaming deferred in P2
        return None

    logger.warning(f"Unknown chat event state: {state}")
    return None


def map_agent_tool_event(
    agent_event: Dict[str, Any],
    harness_id: str,
    session_id: str,
) -> Optional[ObserveEvent]:
    """Map OpenClaw agent tool event to ObserveEvent.

    Agent tool events format:
    - {stream: "tool", phase: "start", toolCallId, name, args} → tool_call
    - {stream: "tool", phase: "result", toolCallId, result} → tool_result
    """
    stream = agent_event.get("stream")
    phase = agent_event.get("phase")
    run_id = agent_event.get("runId", "")
    session_key = agent_event.get("sessionKey", session_id)

    tick_id = run_id or f"tick_{uuid.uuid4().hex[:8]}"
    call_id = agent_event.get("toolCallId", "")

    if stream == "tool" and phase == "start":
        tool_name = agent_event.get("name", "")
        args = agent_event.get("args", {})

        return tool_call(
            harness_type="openclaw",
            harness_id=harness_id,
            session_id=session_key,
            tick_id=tick_id,
            tool_name=tool_name,
            arguments=args if isinstance(args, dict) else {},
            call_id=call_id,
        )

    elif stream == "tool" and phase == "result":
        result = agent_event.get("result")
        error = agent_event.get("error", "")

        return tool_result(
            harness_type="openclaw",
            harness_id=harness_id,
            session_id=session_key,
            tick_id=tick_id,
            call_id=call_id,
            result=result,
            error=error if error else "",
        )

    return None


# ── OpenClaw Gateway Client ────────────────────────────────────────────

class OpenClawGatewayClient:
    """WebSocket client to OpenClaw gateway (port 18789).

    Responsibilities:
    - Connect to gateway WS endpoint
    - Send hello/connect frame with protocol version 4
    - Subscribe to sessions.messages.subscribe for target session
    - Receive ChatEvent and agent tool events
    - Map to ObserveEvent schema
    - Push to observe-service WS ingest endpoint
    """

    def __init__(
        self,
        gateway_url: str = f"ws://{GATEWAY_DEFAULT_HOST}:{GATEWAY_DEFAULT_PORT}",
        observe_ingest_url: str = "ws://localhost:8002/ws/ingest",
        session_key: str = "",
        harness_id: str = "",
    ):
        self.gateway_url = gateway_url
        self.observe_ingest_url = observe_ingest_url
        self.session_key = session_key
        self.harness_id = harness_id or f"openclaw_{uuid.uuid4().hex[:8]}"
        self.req_id = 0
        self.running = False

        # Connection storage (for send_message)
        self.gateway_ws = None
        self.observe_ws = None

        # Event handlers
        self.on_observe_event: Optional[Callable[[ObserveEvent], None]] = None

    async def connect(self) -> None:
        """Connect to OpenClaw gateway and observe-service."""
        logger.info(f"Connecting to OpenClaw gateway: {self.gateway_url}")
        logger.info(f"Connecting to observe ingest: {self.observe_ingest_url}")

        # Connect to observe-service ingest
        self.observe_ws = await ws_client.connect(self.observe_ingest_url)
        await self.observe_ws.send(
            json.dumps({
                "type": "register",
                "harness_type": "openclaw",
                "session_id": self.session_key,
                "harness_id": self.harness_id,
            })
        )
        logger.info("Registered to observe-service")

        # Connect to OpenClaw gateway
        self.gateway_ws = await ws_client.connect(self.gateway_url)

        # Send hello frame
        hello_frame = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": "observe-gateway",
                "displayName": "Observe OpenClaw Gateway",
                "version": "1.0.0",
                "platform": "python",
                "mode": "client",
            },
        }
        await self.gateway_ws.send(json.dumps(hello_frame))
        logger.info("Sent hello to OpenClaw gateway")

        # Wait for hello-ok response
        hello_resp = await self.gateway_ws.recv()
        hello_data = json.loads(hello_resp)
        if hello_data.get("type") != "hello-ok":
            logger.error(f"Unexpected hello response: {hello_data}")
            await self.gateway_ws.close()
            await self.observe_ws.close()
            return

        logger.info(f"Connected to gateway (protocol {hello_data.get('protocol')})")

        # Subscribe to session messages
        if self.session_key:
            await self._send_request(
                self.gateway_ws,
                "sessions.messages.subscribe",
                {"key": self.session_key},
            )
            logger.info(f"Subscribed to session: {self.session_key}")

        self.running = True

        # Main event loop
        try:
            while self.running:
                msg = await self.gateway_ws.recv()
                data = json.loads(msg)

                # Handle event frames
                if data.get("type") == FRAME_EVENT:
                    event_name = data.get("event", "")
                    payload = data.get("payload", {})

                    # Map chat events
                    if event_name == "chat":
                        observe_event = map_chat_event_to_observe(
                            payload,
                            self.harness_id,
                            self.session_key,
                        )
                        if observe_event:
                            # Call event handler first (sync callback for E2E test)
                            if self.on_observe_event:
                                if asyncio.iscoroutinefunction(self.on_observe_event):
                                    await self.on_observe_event(observe_event)
                                else:
                                    self.on_observe_event(observe_event)

                            # Send to observe-service
                            try:
                                await self.observe_ws.send(
                                    json.dumps({
                                        "type": "event",
                                        "payload": observe_event.to_dict(),
                                    })
                                )
                            except Exception as e:
                                logger.error(f"Failed to send event to observe-service: {e}")

                    # Map agent tool events
                    elif event_name == "agent":
                        observe_event = map_agent_tool_event(
                            payload,
                            self.harness_id,
                            self.session_key,
                        )
                        if observe_event:
                            # Call event handler first (sync callback for E2E test)
                            if self.on_observe_event:
                                if asyncio.iscoroutinefunction(self.on_observe_event):
                                    await self.on_observe_event(observe_event)
                                else:
                                    self.on_observe_event(observe_event)

                            # Send to observe-service
                            try:
                                await self.observe_ws.send(
                                    json.dumps({
                                        "type": "event",
                                        "payload": observe_event.to_dict(),
                                    })
                                )
                            except Exception as e:
                                logger.error(f"Failed to send event to observe-service: {e}")

        except Exception as e:
            logger.error(f"Error in event loop: {e}")
        finally:
            logger.info("Closing connections")
            await self.gateway_ws.close()
            await self.observe_ws.close()
            self.running = False

    async def _send_request(
        self,
        ws,
        method: str,
        params: Dict[str, Any],
    ) -> None:
        """Send a request frame to gateway."""
        self.req_id += 1
        req_id = f"req_{self.req_id}"
        frame = serialize_request_frame(req_id, method, params)
        await ws.send(frame)

    async def send_message(
        self,
        message: str,
        agent_id: Optional[str] = None,
        thinking: Optional[str] = None,
    ) -> None:
        """Send a message to OpenClaw (interactive routing).

        Called by observe-service /send endpoint to drive OpenClaw turns.
        Uses chat.send RPC method with GatewayFrame request.

        Args:
            message: User message to send to OpenClaw
            agent_id: Optional agent ID (uses session default if omitted)
            thinking: Optional thinking level setting
        """
        if not self.gateway_ws or not self.running:
            logger.error("Cannot send message: gateway not connected")
            return

        # Build chat.send params (ChatSendParams)
        params = {
            "sessionKey": self.session_key,
            "message": message,
            "idempotencyKey": f"send_{uuid.uuid4().hex}",
        }

        # Add optional params
        if agent_id:
            params["agentId"] = agent_id
        if thinking:
            params["thinking"] = thinking

        # Send request frame
        await self._send_request(
            self.gateway_ws,
            "chat.send",
            params,
        )

        logger.info(f"Sent message to {self.session_key}: {message[:50]}...")

    def stop(self) -> None:
        """Stop the gateway client."""
        self.running = False


# ── Main Entry (for testing) ───────────────────────────────────────────

async def main():
    """Test entry point for OpenClaw gateway client."""
    client = OpenClawGatewayClient(
        session_key="agent:test:test-session",
    )

    async def handle_event(event: ObserveEvent):
        logger.info(f"Observe event: {event.event_type.value} - {event.tick_id}")

    client.on_observe_event = handle_event

    try:
        await client.connect()
    except KeyboardInterrupt:
        logger.info("Interrupted")
        client.stop()


if __name__ == "__main__":
    asyncio.run(main())
