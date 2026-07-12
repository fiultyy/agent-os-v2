#!/usr/bin/env python3
"""Mock OpenClaw Gateway for E2E testing.

Simulates OpenClaw gateway on port 18789:
- Sends hello-ok response (no auth challenge)
- Responds to sessions.messages.subscribe
- Simulates turn events: chat final + agent tool start/result
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from websockets.server import serve

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 4


async def handle_gateway(websocket):
    """Handle mock gateway connections."""
    logger.info("Mock gateway: Connection received")

    try:
        # Receive hello frame
        hello_msg = await websocket.recv()
        hello_data = json.loads(hello_msg)
        logger.info(f"Received hello: {hello_data.get('client', {}).get('id')}")

        # Send hello-ok (no challenge)
        hello_ok = {
            "type": "hello-ok",
            "protocol": PROTOCOL_VERSION,
            "server": {
                "version": "mock-1.0.0",
                "connId": f"mock_{uuid.uuid4().hex[:8]}",
            },
            "features": {
                "methods": ["chat.send", "sessions.messages.subscribe"],
                "events": ["chat", "agent"],
            },
            "snapshot": {},
            "auth": {
                "role": "mock",
                "scopes": ["*"],
            },
            "policy": {
                "maxPayload": 10000000,
                "maxBufferedBytes": 100000000,
                "tickIntervalMs": 1000,
            },
        }
        await websocket.send(json.dumps(hello_ok))
        logger.info("Sent hello-ok")

        # Handle request frames
        async for message in websocket:
            data = json.loads(message)

            if data.get("type") == "req":
                method = data.get("method", "")
                req_id = data.get("id", "")
                params = data.get("params", {})

                logger.info(f"Received request: {method}")

                # Handle sessions.messages.subscribe
                if method == "sessions.messages.subscribe":
                    response = {
                        "type": "res",
                        "id": req_id,
                        "ok": True,
                        "payload": {"subscribed": True},
                    }
                    await websocket.send(json.dumps(response))
                    logger.info(f"Subscribed to session: {params.get('key')}")

                    # Simulate turn events after subscription
                    await asyncio.sleep(1)

                    # Send agent tool start event
                    run_id = f"run_{uuid.uuid4().hex[:8]}"
                    session_key = params.get("key", "mock-session")

                    agent_start_event = {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "phase": "start",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "toolCallId": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "Bash",
                            "args": {"command": "ls -la"},
                        },
                        "seq": 1,
                    }
                    await websocket.send(json.dumps(agent_start_event))
                    logger.info("Sent agent tool start event")

                    await asyncio.sleep(0.5)

                    # Send agent tool result event
                    agent_result_event = {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "phase": "result",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "toolCallId": agent_start_event["payload"]["toolCallId"],
                            "result": "mock output\nfile1.txt\nfile2.txt\n",
                        },
                        "seq": 2,
                    }
                    await websocket.send(json.dumps(agent_result_event))
                    logger.info("Sent agent tool result event")

                    await asyncio.sleep(0.5)

                    # Send chat final event
                    chat_final_event = {
                        "type": "event",
                        "event": "chat",
                        "payload": {
                            "state": "final",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "message": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Mock response: Files listed successfully.",
                                    }
                                ]
                            },
                            "usage": {"inputTokens": 10, "outputTokens": 20},
                            "stopReason": "stop",
                        },
                        "seq": 3,
                    }
                    await websocket.send(json.dumps(chat_final_event))
                    logger.info("Sent chat final event")

                # Handle chat.send
                elif method == "chat.send":
                    response = {
                        "type": "res",
                        "id": req_id,
                        "ok": True,
                        "payload": {
                            "runId": f"run_{uuid.uuid4().hex[:8]}",
                            "sessionKey": params.get("sessionKey"),
                        },
                    }
                    await websocket.send(json.dumps(response))
                    logger.info(f"Sent chat.send response")

                    # Simulate events after chat.send
                    await asyncio.sleep(1)

                    # Send tool events + chat final (same sequence as subscribe)
                    run_id = response["payload"]["runId"]
                    session_key = params.get("sessionKey", "mock-session")

                    agent_start_event = {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "phase": "start",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "toolCallId": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "Bash",
                            "args": {"command": "ls -la"},
                        },
                        "seq": 10,
                    }
                    await websocket.send(json.dumps(agent_start_event))

                    await asyncio.sleep(0.5)

                    agent_result_event = {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "phase": "result",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "toolCallId": agent_start_event["payload"]["toolCallId"],
                            "result": "mock output\nfile1.txt\nfile2.txt\n",
                        },
                        "seq": 11,
                    }
                    await websocket.send(json.dumps(agent_result_event))

                    await asyncio.sleep(0.5)

                    chat_final_event = {
                        "type": "event",
                        "event": "chat",
                        "payload": {
                            "state": "final",
                            "runId": run_id,
                            "sessionKey": session_key,
                            "message": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Mock response: Files listed successfully.",
                                    }
                                ]
                            },
                            "usage": {"inputTokens": 10, "outputTokens": 20},
                            "stopReason": "stop",
                        },
                        "seq": 12,
                    }
                    await websocket.send(json.dumps(chat_final_event))
                    logger.info("Sent events after chat.send")

                else:
                    # Unknown method
                    response = {
                        "type": "res",
                        "id": req_id,
                        "ok": False,
                        "error": {"code": "unknown_method", "message": f"Unknown method: {method}"},
                    }
                    await websocket.send(json.dumps(response))

    except Exception as e:
        logger.error(f"Error in gateway handler: {e}")
    finally:
        logger.info("Mock gateway: Connection closed")


async def main():
    """Start mock OpenClaw gateway on port 18789."""
    logger.info("Starting mock OpenClaw gateway on port 18789")

    async with serve(handle_gateway, "localhost", 18789):
        logger.info("Mock gateway ready")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Mock gateway stopped")
