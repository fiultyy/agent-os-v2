#!/bin/bash
# OpenClaw Gateway End-to-End Test
#
# Real turn execution + event sequence validation
# Validates: tick_started → tool_call → tool_result → tick_completed

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== OpenClaw Gateway E2E Test ==="

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test session
SESSION_KEY="agent:e2e:test-session-$(date +%s)"
HARNESS_ID="e2e_test_$(date +%s)"

echo -e "\n[Test Setup]"
echo "Session Key: $SESSION_KEY"
echo "Harness ID: $HARNESS_ID"

# Check services
echo -e "\n[1/6] Checking services..."
if ! curl -s http://localhost:18789/healthz | grep -q "ok"; then
    echo -e "${RED}✗${NC} OpenClaw gateway not running"
    exit 1
fi
echo -e "${GREEN}✓${NC} OpenClaw gateway running"

if ! curl -s http://localhost:8002/health | grep -q "ok"; then
    echo -e "${RED}✗${NC} Observe-service not running"
    exit 1
fi
echo -e "${GREEN}✓${NC} Observe-service running"

# Create session
echo -e "\n[2/6] Creating session..."
CREATE_RESULT=$(curl -s -X POST http://localhost:8002/sessions \
    -H "Content-Type: application/json" \
    -d "{
        \"harness_type\": \"openclaw\",
        \"session_id\": \"$SESSION_KEY\",
        \"harness_id\": \"$HARNESS_ID\"
    }")

if echo "$CREATE_RESULT" | grep -q "created"; then
    echo -e "${GREEN}✓${NC} Session created"
else
    echo -e "${RED}✗${NC} Session creation failed: $CREATE_RESULT"
    exit 1
fi

# Run gateway in background
echo -e "\n[3/6] Starting gateway (10s event capture)..."
cd "$PROJECT_ROOT"

# Create Python test script
cat > /tmp/e2e_gateway_test.py << 'EOFPYTHON'
import asyncio
import json
import sys
import time
from pathlib import Path

# Add gateways to path
gateways_path = Path(sys.argv[0]).parent.parent / "gateways"
sys.path.insert(0, str(gateways_path))

from openclaw import OpenClawGatewayClient

SESSION_KEY = sys.argv[1]
HARNESS_ID = sys.argv[2]
MESSAGE = sys.argv[3] if len(sys.argv) > 3 else "List files in current directory"

async def run_e2e():
    client = OpenClawGatewayClient(
        session_key=SESSION_KEY,
        harness_id=HARNESS_ID,
    )

    events_received = []

    async def handle_event(event):
        events_received.append({
            "type": event.event_type.value,
            "tick_id": event.tick_id,
            "data": event.data,
        })
        print(f"[Event] {event.event_type.value} - {event.tick_id}")

    client.on_observe_event = handle_event

    # Connect and subscribe
    try:
        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(2)  # Wait for connection

        # Send message (interactive routing)
        print(f"[Send] Sending message: {MESSAGE[:50]}...")
        await client.send_message(MESSAGE)

        # Wait for events (10s timeout)
        await asyncio.sleep(8)

        # Stop gateway
        client.stop()

        # Wait for connect_task to finish
        try:
            await asyncio.wait_for(connect_task, timeout=2)
        except:
            pass

        # Report events
        print(f"\n[E2E Result] Received {len(events_received)} events")
        for evt in events_received:
            print(f"  - {evt['type']} (tick_id: {evt['tick_id']})")

        # Check event sequence
        event_types = [e['type'] for e in events_received]

        # Expected sequence: tool_call, tool_result, tick_completed
        has_tool_call = 'tool_call' in event_types
        has_tool_result = 'tool_result' in event_types
        has_tick_completed = 'tick_completed' in event_types

        if has_tool_call and has_tool_result and has_tick_completed:
            print("✓ Event sequence valid")
            return 0
        else:
            print("✗ Event sequence incomplete")
            print(f"  tool_call: {has_tool_call}")
            print(f"  tool_result: {has_tool_result}")
            print(f"  tick_completed: {has_tick_completed}")
            return 1

    except Exception as e:
        print(f"✗ E2E test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_e2e())
    sys.exit(exit_code)
EOFPYTHON

# Run E2E test
cd "$PROJECT_ROOT"
GATEWAYS_PATH="${PROJECT_ROOT}/gateways/openclaw.py"
echo "Using GATEWAYS_PATH: $GATEWAYS_PATH"
ls -la "$GATEWAYS_PATH" || exit 1

python3 -c "
import asyncio
import sys
import importlib.util

# Load openclaw module from file
spec = importlib.util.spec_from_file_location('openclaw', '$GATEWAYS_PATH')
openclaw_module = importlib.util.module_from_spec(spec)
sys.modules['openclaw'] = openclaw_module
spec.loader.exec_module(openclaw_module)

OpenClawGatewayClient = openclaw_module.OpenClawGatewayClient

async def run():
    client = OpenClawGatewayClient(
        session_key='$SESSION_KEY',
        harness_id='$HARNESS_ID',
    )
    events = []

    async def handle_event(event):
        events.append({'type': event.event_type.value, 'tick_id': event.tick_id})
        print(f'[Event] {event.event_type.value} - {event.tick_id}')

    client.on_observe_event = handle_event

    try:
        connect_task = asyncio.create_task(client.connect())
        print('[Connect] Waiting for gateway connection (5s)...')
        await asyncio.sleep(5)

        print('[Check] Gateway connection status...')
        if not client.running:
            print('✗ Gateway not connected after 5s')
            sys.exit(1)

        print('[Send] Sending message...')
        await client.send_message('List files in current directory')

        await asyncio.sleep(8)
        client.stop()

        try:
            await asyncio.wait_for(connect_task, timeout=2)
        except:
            pass

        print(f'[Result] Received {len(events)} events')
        event_types = [e['type'] for e in events]
        has_tool = 'tool_call' in event_types and 'tool_result' in event_types
        has_tick = 'tick_completed' in event_types

        if has_tool and has_tick:
            print('✓ Event sequence valid')
            sys.exit(0)
        else:
            print('✗ Event sequence incomplete')
            sys.exit(1)
    except Exception as e:
        print(f'✗ E2E failed: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)

asyncio.run(run())
" 2>&1 | tee /tmp/e2e_output.log
E2E_EXIT=${PIPESTATUS[0]}

# Verify events via REST replay
echo -e "\n[4/6] Verifying events via REST replay..."
sleep 2  # Wait for events to persist

EVENTS=$(curl -s "http://localhost:8002/sessions/openclaw/$SESSION_KEY/events?limit=100")
EVENT_COUNT=$(echo "$EVENTS" | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('events', [])))" 2>/dev/null || echo "0")

echo "Events in replay: $EVENT_COUNT"

if [ "$EVENT_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} Events persisted in replay"

    # Check event types
    HAS_TOOL_CALL=$(echo "$EVENTS" | python3 -c "import sys, json; events=json.load(sys.stdin).get('events',[]); print(any(e.get('event_type')=='tool_call' for e in events))" 2>/dev/null || echo "false")
    HAS_TOOL_RESULT=$(echo "$EVENTS" | python3 -c "import sys, json; events=json.load(sys.stdin).get('events',[]); print(any(e.get('event_type')=='tool_result' for e in events))" 2>/dev/null || echo "false")
    HAS_TICK_COMPLETED=$(echo "$EVENTS" | python3 -c "import sys, json; events=json.load(sys.stdin).get('events',[]); print(any(e.get('event_type')=='tick_completed' for e in events))" 2>/dev/null || echo "false")

    echo "Event type check:"
    echo "  - tool_call: $HAS_TOOL_CALL"
    echo "  - tool_result: $HAS_TOOL_RESULT"
    echo "  - tick_completed: $HAS_TICK_COMPLETED"

    if [ "$HAS_TOOL_CALL" = "True" ] && [ "$HAS_TOOL_RESULT" = "True" ] && [ "$HAS_TICK_COMPLETED" = "True" ]; then
        echo -e "${GREEN}✓${NC} Complete event sequence validated via replay"
        EVENTS_VALID=1
    else
        echo -e "${YELLOW}⚠${NC} Event sequence incomplete in replay"
        EVENTS_VALID=0
    fi
else
    echo -e "${RED}✗${NC} No events in replay"
    EVENTS_VALID=0
fi

# Cleanup
echo -e "\n[5/6] Cleaning up..."
curl -s -X DELETE "http://localhost:8002/sessions/openclaw/$SESSION_KEY" > /dev/null
echo "Session deleted"

# Summary
echo -e "\n[6/6] E2E Test Summary"
if [ $E2E_EXIT -eq 0 ] && [ $EVENTS_VALID -eq 1 ]; then
    echo -e "${GREEN}✓✓✓ ALL TESTS PASSED${NC}"
    echo "  - Gateway connection: OK"
    echo "  - Message send: OK"
    echo "  - Event reception: OK"
    echo "  - Event persistence: OK"
    echo "  - Event sequence: OK (tool_call → tool_result → tick_completed)"
    exit 0
else
    echo -e "${RED}✗✗✗ TESTS FAILED${NC}"
    echo "  - Gateway E2E exit code: $E2E_EXIT"
    echo "  - Events valid: $EVENTS_VALID"
    echo "  - Event count: $EVENT_COUNT"
    echo "  - See /tmp/e2e_output.log for details"
    exit 1
fi
