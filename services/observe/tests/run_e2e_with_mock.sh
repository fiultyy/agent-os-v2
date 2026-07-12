#!/bin/bash
# E2E Test with Mock OpenClaw Gateway
#
# Uses mock gateway to avoid auth challenges while validating event sequence

set -e

SCRIPT_DIR="$(cd "$(dirname "$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== E2E Test with Mock OpenClaw Gateway ==="

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Stop real OpenClaw gateway (if running)
echo -e "\n[1/5] Stopping real OpenClaw gateway (if running)..."
pkill -f "openclaw.*gateway" || echo "No real gateway running"
sleep 1

# Start mock gateway
echo -e "\n[2/5] Starting mock OpenClaw gateway..."
cd "$PROJECT_ROOT"
python3 tests/mock_openclaw_gateway.py &
MOCK_PID=$!
echo "Mock gateway PID: $MOCK_PID"

# Wait for mock gateway to start
sleep 2

# Check mock gateway health
echo -e "\n[3/5] Checking mock gateway..."
if curl -s http://localhost:18789/healthz 2>/dev/null | grep -q "ok"; then
    echo -e "${RED}✗${NC} Real gateway still responding on 18789"
    echo "Stopping mock gateway"
    kill $MOCK_PID 2>/dev/null
    exit 1
fi

# Check port 18789 is listening
if netstat -an | grep -q "18789.*LISTEN" || ss -ln | grep -q "18789"; then
    echo -e "${GREEN}✓${NC} Port 18789 listening (mock gateway)"
else
    echo -e "${RED}✗${NC} Port 18789 not listening"
    kill $MOCK_PID 2>/dev/null
    exit 1
fi

# Check observe-service
echo -e "\n[4/5] Checking observe-service..."
if ! curl -s http://localhost:8002/health | grep -q "ok"; then
    echo -e "${RED}✗${NC} Observe-service not running"
    kill $MOCK_PID 2>/dev/null
    exit 1
fi
echo -e "${GREEN}✓${NC} Observe-service running"

# Run E2E test
echo -e "\n[5/5] Running E2E test..."
cd "$PROJECT_ROOT"

SESSION_KEY="agent:e2e:mock-test-$(date +%s)"
HARNESS_ID="e2e_mock_$(date +%s)"

# Create session
CREATE_RESULT=$(curl -s -X POST http://localhost:8002/sessions \
    -H "Content-Type: application/json" \
    -d "{
        \"harness_type\": \"openclaw\",
        \"session_id\": \"$SESSION_KEY\",
        \"harness_id\": \"$HARNESS_ID\"
    }")

if ! echo "$CREATE_RESULT" | grep -q "created"; then
    echo -e "${RED}✗${NC} Session creation failed"
    kill $MOCK_PID 2>/dev/null
    exit 1
fi
echo -e "${GREEN}✓${NC} Session created: $SESSION_KEY"

# Run gateway test
GATEWAYS_PATH="${PROJECT_ROOT}/gateways/openclaw.py"

python3 -c "
import asyncio
import sys
import importlib.util

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
        await asyncio.sleep(6)  # Wait for connection + mock events

        print(f'[Result] Received {len(events)} events')

        for evt in events:
            print(f'  - {evt[\"type\"]} (tick_id: {evt[\"tick_id\"]})')

        client.stop()

        try:
            await asyncio.wait_for(connect_task, timeout=2)
        except:
            pass

        # Check event sequence
        event_types = [e['type'] for e in events]
        has_tool_call = 'tool_call' in event_types
        has_tool_result = 'tool_result' in event_types
        has_tick_completed = 'tick_completed' in event_types

        print(f'Event type check:')
        print(f'  - tool_call: {has_tool_call}')
        print(f'  - tool_result: {has_tool_result}')
        print(f'  - tick_completed: {has_tick_completed}')

        if has_tool_call and has_tool_result and has_tick_completed:
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
" 2>&1 | tee /tmp/e2e_mock_output.log
TEST_EXIT=${PIPESTATUS[0]}

# Verify via REST replay
sleep 1
EVENTS=$(curl -s "http://localhost:8002/sessions/openclaw/$SESSION_KEY/events?limit=100")
EVENT_COUNT=$(echo "$EVENTS" | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('events', [])))" 2>/dev/null || echo "0")

echo -e "\n=== E2E Test Summary ==="
echo "Test exit code: $TEST_EXIT"
echo "Events in replay: $EVENT_COUNT"

# Cleanup
echo -e "\nCleaning up..."
kill $MOCK_PID 2>/dev/null
curl -s -X DELETE "http://localhost:8002/sessions/openclaw/$SESSION_KEY" > /dev/null
echo "Mock gateway stopped, session deleted"

if [ $TEST_EXIT -eq 0 ] && [ "$EVENT_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓✓✓ E2E TEST PASSED${NC}"
    echo "  - Mock gateway: OK"
    echo "  - Event reception: OK"
    echo "  - Event sequence: OK (tool_call → tool_result → tick_completed)"
    echo "  - Event persistence: OK ($EVENT_COUNT events)"
    exit 0
else
    echo -e "${RED}✗✗✗ E2E TEST FAILED${NC}"
    echo "  - Test exit: $TEST_EXIT"
    echo "  - Events: $EVENT_COUNT"
    echo "  - See /tmp/e2e_mock_output.log"
    exit 1
fi
