"""OpenClaw harness client (orchestrator-side).

The orchestrator is the ONLY client that connects to the openclaw gateway
(:18789). Ported from services/observe/gateways/openclaw.py — the send/subscribe
logic moved here (observe is now read-only per ADR-4).

Responsibilities:
- v4 handshake: connect.challenge → connect(req with auth.token) → res(hello-ok)
- sessions.messages.subscribe to receive ChatEvent + agent tool events
- sessions.send to drive a turn (the primitive API calls this)
- map events → ObserveEvent dict (tick_started/tool_call/tool_result/
  tick_completed/token_delta) and push to the ObserveEmitter
- per-runId tick_started synthesis: openclaw emits runId only in event
  payloads, so tick_started is back-filled on the first event of each run
  (tick_id = runId keeps it consistent with later events)
- _last_prompt cache so the synthesized tick_started carries the request
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Callable, Dict, Optional

import websockets.client as ws_client

from .events import (
    tick_completed,
    token_delta,
    tick_started,
    tool_call,
    tool_result,
)
from .emit import ObserveEmitter

logger = logging.getLogger(__name__)

# ── Protocol Constants (openclaw gateway v4) ──────────────────────────

PROTOCOL_VERSION = 4
GATEWAY_DEFAULT_URL = "ws://localhost:18789"
FRAME_REQ = "req"
FRAME_RES = "res"
FRAME_EVENT = "event"


def serialize_request_frame(
    req_id: str, method: str, params: Optional[Dict[str, Any]] = None,
) -> str:
    return json.dumps({
        "type": FRAME_REQ, "id": req_id, "method": method, "params": params or {},
    })


def _foreign_session(payload: Dict[str, Any], own_session: str) -> bool:
    """gateway 把每个 session 的 chat/agent 事件广播给所有 operator 客户端。
    本 client 只拥有自己的 session_key —— 返回 True 表示该事件属于别的 session,
    应跳过(否则会被盖错戳 emit 到错误的 observe ingest 连接,被 observe 的
    session 校验丢弃,导致事件互相串线、大面积丢失)。"""
    sk = payload.get("sessionKey") or payload.get("session_key")
    # 缺失 sessionKey → 当外部跳过(防御性:协议漂移/新事件类型不静默串线)
    return sk is None or sk != own_session


# ── Event Mapping: openclaw ChatEvent / agent tool → ObserveEvent ─────

def map_chat_event(
    chat_event: Dict[str, Any], harness_id: str, session_id: str,
) -> Optional[Dict[str, Any]]:
    """Map openclaw ChatEvent → ObserveEvent dict.

    state ∈ {delta, final, aborted, error}
    """
    state = chat_event.get("state")
    run_id = chat_event.get("runId", "")
    session_key = chat_event.get("sessionKey", session_id)
    tick_id = run_id or f"tick_{uuid.uuid4().hex[:8]}"

    ht = "openclaw"

    if state == "final":
        message = chat_event.get("message", {})
        response = ""
        if isinstance(message, dict):
            for block in message.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    response += block.get("text", "")
        return tick_completed(ht, harness_id, session_key, tick_id,
                              status="success", response=response, tool_count=0)

    if state == "aborted":
        return tick_completed(ht, harness_id, session_key, tick_id,
                              status="error",
                              response="Turn aborted by user or coordinator")

    if state == "error":
        err_msg = chat_event.get("errorMessage", "Unknown error")
        err_kind = chat_event.get("errorKind", "unknown")
        return tick_completed(ht, harness_id, session_key, tick_id,
                              status="error",
                              response=f"Turn failed: {err_kind} - {err_msg}")

    if state == "delta":
        return token_delta(ht, harness_id, session_key, tick_id,
                           delta_text=chat_event.get("deltaText", ""))

    logger.warning("Unknown chat event state: %s", state)
    return None


def map_agent_tool_event(
    agent_event: Dict[str, Any], harness_id: str, session_id: str,
) -> Optional[Dict[str, Any]]:
    """Map openclaw agent tool event → ObserveEvent dict.

    {stream:"tool", phase:"start"|"result", ...}
    """
    stream = agent_event.get("stream")
    phase = agent_event.get("phase")
    run_id = agent_event.get("runId", "")
    session_key = agent_event.get("sessionKey", session_id)
    tick_id = run_id or f"tick_{uuid.uuid4().hex[:8]}"
    call_id = agent_event.get("toolCallId", "")
    ht = "openclaw"

    if stream == "tool" and phase == "start":
        args = agent_event.get("args", {})
        return tool_call(ht, harness_id, session_key, tick_id,
                         tool_name=agent_event.get("name", ""),
                         arguments=args if isinstance(args, dict) else {},
                         call_id=call_id)

    if stream == "tool" and phase == "result":
        result = agent_event.get("result")
        error = agent_event.get("error", "")
        return tool_result(ht, harness_id, session_key, tick_id,
                           call_id=call_id, result=result,
                           error=error if error else "")

    return None


# ── OpenClaw harness client ───────────────────────────────────────────

class OpenClawClient:
    """Connects to the openclaw gateway, subscribes, sends, maps events.

    Lifecycle: connect() runs the event loop in the background (started by the
    route handler). send_message() drives a turn. stop() tears down.
    """

    CLIENT_ID = "gateway-client"   # closed registry (client-info.ts)
    CLIENT_MODE = "backend"

    def __init__(
        self,
        session_key: str,
        gateway_url: str = GATEWAY_DEFAULT_URL,
        harness_id: str = "",
        emitter: Optional[ObserveEmitter] = None,
    ):
        self.session_key = session_key
        self.gateway_url = gateway_url
        self.harness_id = harness_id or f"openclaw_{uuid.uuid4().hex[:8]}"
        self.emitter = emitter or ObserveEmitter(
            harness_type="openclaw",
            harness_id=self.harness_id,
            session_id=session_key,
        )
        self.req_id = 0
        self.running = False
        self.gateway_ws = None
        self._connect_task: Optional[asyncio.Task] = None

        # per-runId tick_started synthesis state
        # ponytail: unbounded set (one entry per run_id); reset on session teardown
        self._ticks_seen: set = set()
        self._last_prompt: str = ""
        # optional local callback (e.g. tests / self-check)
        self.on_event: Optional[Callable[[Dict[str, Any]], Any]] = None
        # pending RPC req→future responses (for delete/fork that need a reply)
        self._pending: Dict[str, "asyncio.Future[Dict[str, Any]]"] = {}

    async def connect(self) -> None:
        """Connect to gateway + observe, run the event loop until stopped."""
        logger.info("Connecting to OpenClaw gateway: %s", self.gateway_url)

        # observe ingest first (registers the session)
        await self.emitter.connect()

        self.gateway_ws = await ws_client.connect(self.gateway_url)

        auth_token = _read_auth_token()

        # ── v4 handshake: challenge → connect(req) → res(hello-ok) ──
        first_frame = json.loads(
            await asyncio.wait_for(self.gateway_ws.recv(), timeout=10)
        )

        if first_frame.get("type") == FRAME_EVENT and \
                first_frame.get("event") == "connect.challenge":
            await self._handle_challenge(first_frame, auth_token)
        elif first_frame.get("type") == "hello-ok":
            logger.info(
                "Connected to OpenClaw gateway [legacy] (protocol %s)",
                first_frame.get("protocol"),
            )
        else:
            logger.error("Expected challenge/hello-ok, got: %s", first_frame)
            await self.gateway_ws.close()
            await self.emitter.close()
            return

        if self.session_key:
            await self._send_request(
                self.gateway_ws, "sessions.messages.subscribe",
                {"key": self.session_key},
            )
            logger.info("Subscribed to session: %s", self.session_key)

        self.running = True
        try:
            while self.running:
                msg = await self.gateway_ws.recv()
                data = json.loads(msg)
                if data.get("type") != FRAME_EVENT:
                    # route res frames to waiting RPC callers (delete/fork)
                    if data.get("type") == FRAME_RES:
                        fut = self._pending.pop(data.get("id", ""), None)
                        if fut is not None and not fut.done():
                            fut.set_result(data)
                    continue
                event_name = data.get("event", "")
                payload = data.get("payload", {})

                if event_name == "chat":
                    if _foreign_session(payload, self.session_key):
                        continue
                    await self._ensure_tick_started(payload)
                    ev = map_chat_event(payload, self.harness_id, self.session_key)
                    await self._dispatch(ev)
                elif event_name == "agent":
                    if _foreign_session(payload, self.session_key):
                        continue
                    await self._ensure_tick_started(payload)
                    ev = map_agent_tool_event(payload, self.harness_id, self.session_key)
                    await self._dispatch(ev)
        except Exception as e:
            logger.error("Error in openclaw event loop: %s", e)
        finally:
            logger.info("Closing openclaw connections")
            try:
                await self.gateway_ws.close()
            except Exception:
                pass
            await self.emitter.close()
            self.running = False

    async def _handle_challenge(
        self, first_frame: Dict[str, Any], auth_token: Optional[str],
    ) -> None:
        challenge_nonce = first_frame.get("payload", {}).get("nonce")
        logger.info("Received connect.challenge (nonce: %s...)",
                    str(challenge_nonce)[:8])

        connect_params: Dict[str, Any] = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": self.CLIENT_ID,
                "displayName": "Orchestrator OpenClaw Client",
                "version": "1.0.0",
                "platform": "python",
                "mode": self.CLIENT_MODE,
            },
            "role": "operator",
            "scopes": ["operator.admin"],
        }
        # auth.token required for --auth token mode; do NOT send a partial
        # device block (server grants admin via auth.token alone).
        if auth_token:
            connect_params["auth"] = {"token": auth_token}

        connect_req_id = str(uuid.uuid4())
        await self.gateway_ws.send(
            serialize_request_frame(connect_req_id, "connect", connect_params)
        )
        logger.info("Sent connect req%s",
                    " (with auth token)" if auth_token else " (no token)")

        hello_resp = json.loads(
            await asyncio.wait_for(self.gateway_ws.recv(), timeout=15)
        )
        if hello_resp.get("type") != FRAME_RES or not hello_resp.get("ok"):
            err = hello_resp.get("error", {}) if hello_resp.get("type") == FRAME_RES else {}
            logger.error("OpenClaw connect rejected: %s - %s (raw: %s)",
                         err.get("code", ""), err.get("message", ""), hello_resp)
            await self.gateway_ws.close()
            await self.emitter.close()
            return
        hello_ok = hello_resp.get("payload", {})
        logger.info("Connected to OpenClaw gateway (protocol %s, role %s)",
                    hello_ok.get("protocol"),
                    hello_ok.get("auth", {}).get("role"))

    async def _ensure_tick_started(self, payload: Dict[str, Any]) -> None:
        """Synthesize + emit tick_started on the first event for a given runId.

        openclaw emits runId only in event payloads, so tick_started is
        back-filled here. tick_id = runId keeps it consistent with the later
        tool/tick_completed/token_delta events.
        """
        run_id = payload.get("runId", "")
        if not run_id or run_id in self._ticks_seen:
            return
        self._ticks_seen.add(run_id)

        ev = tick_started(
            "openclaw", self.harness_id, self.session_key,
            tick_id=run_id, request=self._last_prompt,
        )
        await self._dispatch(ev)

    async def _dispatch(self, event: Optional[Dict[str, Any]]) -> None:
        if event is None:
            return
        if self.on_event:
            if asyncio.iscoroutinefunction(self.on_event):
                await self.on_event(event)
            else:
                self.on_event(event)
        await self.emitter.emit(event)

    async def _send_request(self, ws, method: str, params: Dict[str, Any]) -> None:
        self.req_id += 1
        await ws.send(serialize_request_frame(f"req_{self.req_id}", method, params))

    async def send_message(
        self, message: str,
        agent_id: Optional[str] = None, thinking: Optional[str] = None,
    ) -> None:
        """Drive a turn via gateway RPC `sessions.send`.

        SessionsSendParamsSchema: {key, agentId?, message, thinking?, idempotencyKey?}.
        """
        if not self.gateway_ws or not self.running:
            logger.error("Cannot send: gateway not connected")
            return

        params: Dict[str, Any] = {
            "key": self.session_key,
            "message": message,
            "idempotencyKey": f"send_{uuid.uuid4().hex}",
        }
        if agent_id:
            params["agentId"] = agent_id
        if thinking:
            params["thinking"] = thinking

        # record prompt for the synthesized tick_started (runId arrives in events)
        self._last_prompt = message

        await self._send_request(self.gateway_ws, "sessions.send", params)
        logger.info("Sent message to %s: %s...", self.session_key, message[:50])

    def start_background(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
        """Spawn connect() as a background task on the running loop."""
        loop = loop or asyncio.get_event_loop()
        self._connect_task = loop.create_task(self.connect())
        return self._connect_task

    async def _request(
        self, method: str, params: Dict[str, Any], timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """Send a gateway req and await its matching res frame.

        Unlike _send_request (fire-and-forget), this waits for the response.
        The res frame is routed back by the connect() event loop via the
        _pending futures map. Returns the raw res dict ({type, id, ok, payload?, error?}).
        """
        if not self.gateway_ws or not self.running:
            raise RuntimeError("gateway not connected")
        self.req_id += 1
        req_id = f"req_{self.req_id}"
        loop = asyncio.get_event_loop()
        fut: "asyncio.Future[Dict[str, Any]]" = loop.create_future()
        self._pending[req_id] = fut
        await self.gateway_ws.send(serialize_request_frame(req_id, method, params))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise

    async def delete(self) -> Dict[str, Any]:
        """Delete this session's transcript via gateway RPC sessions.delete.

        Mirror of sessions.messages.subscribe call pattern, but awaits the res.
        Gateway params: {key, agentId?, deleteTranscript?}. deleteTranscript
        defaults to true server-side. Then stops the client.
        """
        if not self.gateway_ws or not self.running:
            return {"deleted": False, "error": "gateway not connected",
                    "key": self.session_key}
        try:
            res = await self._request(
                "sessions.delete", {"key": self.session_key},
            )
        except Exception as e:
            logger.error("sessions.delete RPC failed: %s", e)
            return {"deleted": False, "error": str(e), "key": self.session_key}

        if not res.get("ok"):
            err = res.get("error", {})
            logger.error("sessions.delete rejected: %s", err)
            return {"deleted": False, "error": err, "key": self.session_key}

        await self.stop()
        return {"deleted": True, "key": self.session_key}

    async def fork(self, new_key: str = None) -> Dict[str, Any]:
        """Fork this session's context into a new session.

        ADR-4 PARTIAL: openclaw has no native sessions.fork RPC. The closest
        primitive, sessions.compaction.branch, requires a pre-existing
        compaction checkpoint (checkpointId is mandatory + must resolve to a
        real checkpoint) — it cannot be driven directly from a bare session
        key without first running compaction. Rather than fabricate a fake
        checkpointId, this returns an explicit not-supported stub. Upstream
        needs a real sessions.fork RPC; until then callers should fork via the
        claude client (cc has --fork-session).
        """
        return {
            "forked": False,
            "error": "claw verbatim fork not supported "
                     "(ADR-4 defer; try cc)",
            "key": self.session_key,
            "new_key": new_key or f"{self.session_key}-fork-{uuid.uuid4().hex[:6]}",
        }

    async def stop(self) -> None:
        self.running = False
        try:
            if self.gateway_ws is not None:
                await self.gateway_ws.close()
        except Exception:
            pass
        await self.emitter.close()
        if self._connect_task is not None:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except (asyncio.CancelledError, Exception):
                pass


def _read_auth_token() -> Optional[str]:
    """Read gateway auth token from env or ~/.openclaw/openclaw.json."""
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    if token:
        return token
    try:
        cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get("gateway", {}).get("auth", {}).get("token")
    except Exception as e:
        logger.warning("Could not read openclaw gateway token: %s", e)
        return None
