#!/usr/bin/env python3
"""Mock Openclaw Gateway: simulate openclaw turn sequence.

模拟 openclaw 行为，发送标准 turn 序列到 observe-service:
- tick_started → tool_call → tool_result → tick_completed
- 用于验证 session 切换隔离 + replay
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import websockets


# ── 配置 ─────────────────────────────────────────────────────

OBSERVE_WS_URL = os.getenv("OBSERVE_WS_URL", "ws://localhost:8002/ws/ingest")
HARNESS_TYPE = "openclaw"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Mock Turn Generator ────────────────────────────────────────

async def send_mock_turn(
    harness_type: str,
    session_id: str,
    harness_id: str,
    tick_id: str,
):
    """发送一个完整的 mock turn 序列."""
    base_time = datetime.now(timezone.utc)

    # 1. tick_started
    tick_started_event = {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": "tick_started",
        "data": {"request": "mock user prompt for openclaw"},
        "timestamp": (base_time).isoformat(),
    }

    # 2. tool_call
    tool_call_event = {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": "tool_call",
        "data": {
            "call_id": str(uuid.uuid4()),
            "tool_name": "Read",
            "arguments": {"file_path": "/tmp/openclaw_mock.txt"},
        },
        "timestamp": (base_time + timedelta(seconds=1)).isoformat(),
    }

    # 3. tool_result
    tool_result_event = {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": "tool_result",
        "data": {
            "call_id": tool_call_event["data"]["call_id"],
            "result": "mock file content from openclaw\n",
            "error": "",
        },
        "timestamp": (base_time + timedelta(seconds=2)).isoformat(),
    }

    # 4. tick_completed
    tick_completed_event = {
        "event_id": str(uuid.uuid4()),
        "harness_type": harness_type,
        "harness_id": harness_id,
        "session_id": session_id,
        "tick_id": tick_id,
        "event_type": "tick_completed",
        "data": {
            "status": "success",
            "response": "Mock response from openclaw",
            "tool_count": 1,
            "duration_ms": 3000.0,
        },
        "timestamp": (base_time + timedelta(seconds=3)).isoformat(),
    }

    events = [
        tick_started_event,
        tool_call_event,
        tool_result_event,
        tick_completed_event,
    ]

    return events


# ── Main ───────────────────────────────────────────────────────

async def main():
    """连接 observe-service 并发送 mock turn 序列."""
    import argparse

    parser = argparse.ArgumentParser(description="Mock Openclaw Gateway")
    parser.add_argument("--session-id", required=True, help="Unique session ID (uuid)")
    parser.add_argument(
        "--harness-id", default="mock-openclaw-1", help="Harness instance ID"
    )
    args = parser.parse_args()

    session_id = args.session_id
    harness_id = args.harness_id
    tick_id = str(uuid.uuid4())

    # 构建 WS URL（带 query params）
    ws_url = f"{OBSERVE_WS_URL}?harness_type={HARNESS_TYPE}&session_id={session_id}&harness_id={harness_id}"

    logger.info(f"Mock openclaw gateway connecting to {ws_url}")
    logger.info(f"Session: {HARNESS_TYPE}/{session_id}")

    try:
        async with websockets.connect(ws_url) as ws:
            logger.info("Connected, sending mock turn sequence...")

            # 生成 mock turn 序列
            events = await send_mock_turn(
                HARNESS_TYPE, session_id, harness_id, tick_id
            )

            # 发送每个事件
            for i, event in enumerate(events, 1):
                message = {"type": "event", "payload": event}
                await ws.send(json.dumps(message))
                logger.info(
                    f"Sent event {i}/4: {event['event_type']} (tick_id={tick_id[:8]})"
                )
                # 短暂延迟（模拟真实行为）
                await asyncio.sleep(0.1)

            logger.info(f"Mock turn sequence completed ({len(events)} events sent)")

            # 保持连接一小段时间，确保 observe-service 处理
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Mock gateway error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
