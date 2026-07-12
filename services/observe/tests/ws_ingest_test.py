#!/usr/bin/env python3
"""WebSocket ingest client for e2e test.

连接 /ws/ingest，发送完整 turn 序列：
tick_started → tool_call → tool_result → tick_completed
"""
import asyncio
import json
import sys
import websockets
import uuid
from datetime import datetime, timezone, timedelta

# 测试参数
WS_URL = "ws://localhost:8002/ws/ingest"
HARNESS_TYPE = "mock-test"
# 唯一 session_id: 每次运行隔离
SESSION_ID = f"ws-e2e-{uuid.uuid4().hex[:8]}"
HARNESS_ID = f"probe-{uuid.uuid4().hex[:6]}"

# 生成 turn 序列的 4 个事件（使用真实 timestamp 递增）
def build_turn_sequence():
    tick_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    # 真实时间递增，保证同 session 内严格有序
    base_time = datetime.now(timezone.utc)
    timestamps = [
        base_time.isoformat(),
        (base_time + timedelta(seconds=1)).isoformat(),
        (base_time + timedelta(seconds=2)).isoformat(),
        (base_time + timedelta(seconds=3)).isoformat(),
    ]

    events = [
        # 1. tick_started
        {
            "type": "event",
            "payload": {
                "event_id": str(uuid.uuid4()),
                "harness_type": HARNESS_TYPE,
                "harness_id": HARNESS_ID,
                "session_id": SESSION_ID,
                "tick_id": tick_id,
                "event_type": "tick_started",
                "data": {"request": "e2e test turn"},
                "timestamp": timestamps[0]
            }
        },
        # 2. tool_call
        {
            "type": "event",
            "payload": {
                "event_id": str(uuid.uuid4()),
                "harness_type": HARNESS_TYPE,
                "harness_id": HARNESS_ID,
                "session_id": SESSION_ID,
                "tick_id": tick_id,
                "event_type": "tool_call",
                "data": {
                    "call_id": call_id,
                    "tool_name": "Bash",
                    "arguments": {"command": "echo test"}
                },
                "timestamp": timestamps[1]
            }
        },
        # 3. tool_result
        {
            "type": "event",
            "payload": {
                "event_id": str(uuid.uuid4()),
                "harness_type": HARNESS_TYPE,
                "harness_id": HARNESS_ID,
                "session_id": SESSION_ID,
                "tick_id": tick_id,
                "event_type": "tool_result",
                "data": {
                    "call_id": call_id,
                    "result": "test",
                    "error": ""
                },
                "timestamp": timestamps[2]
            }
        },
        # 4. tick_completed
        {
            "type": "event",
            "payload": {
                "event_id": str(uuid.uuid4()),
                "harness_type": HARNESS_TYPE,
                "harness_id": HARNESS_ID,
                "session_id": SESSION_ID,
                "tick_id": tick_id,
                "event_type": "tick_completed",
                "data": {
                    "status": "success",
                    "response": "e2e test complete",
                    "tool_count": 1,
                    "duration_ms": 3000.0
                },
                "timestamp": timestamps[3]
            }
        },
    ]
    return events

async def send_turn_sequence():
    """连接 WS ingest 并发送完整 turn 序列。"""
    events = build_turn_sequence()
    query_params = f"harness_type={HARNESS_TYPE}&session_id={SESSION_ID}&harness_id={HARNESS_ID}"
    url = f"{WS_URL}?{query_params}"

    try:
        async with websockets.connect(url) as ws:
            # 等待连接确认
            await asyncio.sleep(0.5)

            # 发送 4 个事件
            for ev in events:
                await ws.send(json.dumps(ev))
                await asyncio.sleep(0.1)  # 小延迟模拟真实序列

            # 等待服务端处理
            await asyncio.sleep(0.5)

            print(f"✓ Sent {len(events)} events via WS ingest", file=sys.stderr)
            print(json.dumps({
                "status": "ok",
                "events_sent": len(events),
                "session_id": SESSION_ID
            }))
            return 0

    except Exception as e:
        print(f"✗ WS ingest failed: {e}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(send_turn_sequence())
    sys.exit(exit_code)
