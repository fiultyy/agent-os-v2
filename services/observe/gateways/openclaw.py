"""OpenClaw Gateway: Connect to OpenClaw gateway (port 18789) and bridge to observe-service.

ACP (Agent Client Protocol) long connection + sessions.messages.subscribe.
Events mapping: ChatEvent → ObserveEvent (tick_started, tool_call, tool_result, tick_completed).
Interactive routing: observe /send → openclaw sessions.send.
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
    - v4 handshake: receive connect.challenge → send connect(req) → recv res(hello-ok)
    - Subscribe to sessions.messages.subscribe for target session
    - Receive ChatEvent and agent tool events
    - Map to ObserveEvent schema
    - Push to observe-service WS ingest endpoint
    """

    # GatewayClientId enum (openclaw packages/gateway-protocol/src/client-info.ts).
    # client.id is a closed registry; observe bridge must use "gateway-client".
    CLIENT_ID = "gateway-client"
    CLIENT_MODE = "backend"

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

        # Connect to observe-service ingest (ws_ingest 从 query params 注册 session)
        from urllib.parse import quote
        ingest_url = (
            f"{self.observe_ingest_url}"
            f"?harness_type=openclaw"
            f"&session_id={quote(self.session_key, safe='')}"
            f"&harness_id={quote(self.harness_id, safe='')}"
        )
        self.observe_ws = await ws_client.connect(ingest_url)
        logger.info(f"Registered to observe-service (session={self.session_key})")

        # Connect to OpenClaw gateway
        self.gateway_ws = await ws_client.connect(self.gateway_url)

        # Read gateway auth token (env OPENCLAW_GATEWAY_TOKEN or ~/.openclaw/openclaw.json).
        # openclaw --auth token mode requires connect.params.auth.token.
        auth_token = None
        try:
            import os
            auth_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
            if not auth_token:
                cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
                with open(cfg_path) as f:
                    cfg = json.load(f)
                auth_token = cfg.get("gateway", {}).get("auth", {}).get("token")
        except Exception as e:
            logger.warning(f"Could not read openclaw gateway token: {e}")

        # ── OpenClaw gateway v4 handshake: challenge → connect(req) → res(hello-ok) ──
        # Server PROACTIVELY sends a connect.challenge event frame immediately after the
        # WS upgrade (src/gateway/server/ws-connection.ts:387-392), regardless of auth mode.
        # Client must reply with a RequestFrame {type:"req", method:"connect"} carrying
        # auth.token (token mode) and optionally device.nonce echoing the challenge nonce.
        # See packages/gateway-client/src/client.ts:1325-1405, packages/gateway-protocol/src/schema/frames.ts.
        first_frame = json.loads(await asyncio.wait_for(self.gateway_ws.recv(), timeout=10))

        # Real v4 gateway: first frame is the connect.challenge event.
        if first_frame.get("type") == FRAME_EVENT and first_frame.get("event") == "connect.challenge":
            challenge_nonce = first_frame.get("payload", {}).get("nonce")
            logger.info(f"Received connect.challenge (nonce: {str(challenge_nonce)[:8]}...)")

            # Build the connect request frame (ConnectParamsSchema, frames.ts:30-81).
            connect_req_id = str(uuid.uuid4())
            connect_params: Dict[str, Any] = {
                "minProtocol": PROTOCOL_VERSION,
                "maxProtocol": PROTOCOL_VERSION,
                "client": {
                    "id": self.CLIENT_ID,            # closed enum (client-info.ts GATEWAY_CLIENT_IDS)
                    "displayName": "Observe OpenClaw Gateway",
                    "version": "1.0.0",
                    "platform": "python",
                    "mode": self.CLIENT_MODE,        # GATEWAY_CLIENT_MODES
                },
                "role": "operator",
                "scopes": ["operator.admin"],
            }
            # auth.token required for --auth token mode (src/gateway/auth.ts:562-569)
            if auth_token:
                connect_params["auth"] = {"token": auth_token}
            # NOTE: the challenge nonce is only echoed via the device identity path
            # (frames.ts ConnectParams.device = {id, publicKey, signature, signedAt, nonce}
            # — all required). For token auth we intentionally do NOT send a device block:
            # server grants operator.admin via auth.token alone, and a partial device
            # object triggers INVALID_REQUEST. (message-handler.ts:1148-1155 nonce check
            # only applies when device identity is present.)

            await self.gateway_ws.send(
                serialize_request_frame(connect_req_id, "connect", connect_params)
            )
            logger.info(
                "Sent connect req to OpenClaw gateway"
                + (" (with auth token)" if auth_token else " (no token)")
            )

            # Wait for the connect response: ResponseFrame {type:"res"} whose payload is HelloOk.
            # NOTE: hello-ok lives at payload.type, NOT top-level type (ResponseFrameSchema).
            hello_resp = json.loads(await asyncio.wait_for(self.gateway_ws.recv(), timeout=15))
            if hello_resp.get("type") != FRAME_RES or not hello_resp.get("ok"):
                err = hello_resp.get("error", {}) if hello_resp.get("type") == FRAME_RES else {}
                logger.error(
                    f"OpenClaw connect rejected: {err.get('code', '')} - {err.get('message', '')} "
                    f"(raw: {hello_resp})"
                )
                await self.gateway_ws.close()
                await self.observe_ws.close()
                return
            hello_ok = hello_resp.get("payload", {})
            logger.info(
                f"Connected to OpenClaw gateway (protocol {hello_ok.get('protocol')}, "
                f"role {hello_ok.get('auth', {}).get('role')})"
            )

        # Legacy/stub server (e.g. tests/mock_openclaw_gateway.py) sends a top-level
        # hello-ok directly with no challenge. Accept it for back-compat.
        elif first_frame.get("type") == "hello-ok":
            logger.info(
                f"Connected to OpenClaw gateway [legacy] (protocol {first_frame.get('protocol')})"
            )

        else:
            logger.error(f"Expected connect.challenge or hello-ok, got: {first_frame}")
            await self.gateway_ws.close()
            await self.observe_ws.close()
            return

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
        Uses the gateway RPC method `sessions.send` (SessionsSendParamsSchema:
        {key, agentId?, message, thinking?, idempotencyKey?}). This is the
        agent-turn send path; `send`/`chat.send` are channel/legacy methods.
        """
        if not self.gateway_ws or not self.running:
            logger.error("Cannot send message: gateway not connected")
            return

        # Build sessions.send params (SessionsSendParamsSchema).
        params = {
            "key": self.session_key,
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
            "sessions.send",
            params,
        )

        logger.info(f"Sent message to {self.session_key}: {message[:50]}...")

    def stop(self) -> None:
        """Stop the gateway client."""
        self.running = False


# ── Main Entry (for testing) ───────────────────────────────────────────

async def main():
    """Test entry: real agent:main:main + send turn + verify observe events."""
    client = OpenClawGatewayClient(session_key="agent:main:main")

    events = []
    async def handle_event(event: ObserveEvent):
        logger.info(f"Observe event: {event.event_type.value} - {event.tick_id}")
        events.append(event.event_type.value)
    client.on_observe_event = handle_event

    try:
        connect_task = asyncio.create_task(client.connect())  # background(connect 内 while loop)
        await asyncio.sleep(3)  # 等 connect + auth + subscribe
        await client.send_message("what is 8+8?")
        await asyncio.sleep(30)  # 等 turn 完成
        client.stop()
        await asyncio.sleep(1)
        logger.info(f"All observe events: {events}")
        logger.info("E2E_PASS" if "tick_completed" in events else "E2E_FAIL")
    except KeyboardInterrupt:
        client.stop()


if __name__ == "__main__":
    asyncio.run(main())
