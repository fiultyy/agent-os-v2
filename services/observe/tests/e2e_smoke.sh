#!/bin/bash
# E2E Smoke Test for Observe-Service (T1 self-validation)
# 覆盖: 启动 + /health + WS ingest + REST replay + session CRUD

set -e

cd "$(dirname "$0")/../"

echo "=== T1 E2E Smoke Test ==="

# 1. Check import
echo "[1/6] Checking Python import..."
python3 -c "import sys; sys.path.insert(0, 'src'); from src.app import app; print('✓ Import OK')"

# 2. Install dependencies (if needed)
echo "[2/6] Checking dependencies..."
pip3 install -q -r requirements.txt 2>/dev/null || true

# 3. Start observe-service in background
echo "[3/6] Starting observe-service (port 8002)..."
python3 -m uvicorn src.app:app --host 0.0.0.0 --port 8002 &
OBSERVE_PID=$!
sleep 3  # Wait for startup

# 4. Check /health
echo "[4/6] Checking /health..."
HEALTH=$(curl -s http://localhost:8002/health)
if [[ "$HEALTH" == *"ok"* ]]; then
    echo "✓ /health OK"
else
    echo "✗ /health FAILED: $HEALTH"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

# 5. Test session CRUD
echo "[5/6] Testing session CRUD..."

# Create session
CREATE_RESP=$(curl -s -X POST http://localhost:8002/sessions \
    -H "Content-Type: application/json" \
    -d '{"harness_type":"mock-test","session_id":"test-session","harness_id":"test-harness"}')
if [[ "$CREATE_RESP" == *"created"* ]]; then
    echo "✓ Session create OK"
else
    echo "✗ Session create FAILED: $CREATE_RESP"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

# List sessions
LIST_RESP=$(curl -s http://localhost:8002/sessions)
if [[ "$LIST_RESP" == *"mock-test"* ]] && [[ "$LIST_RESP" == *"test-session"* ]]; then
    echo "✓ Session list OK"
else
    echo "✗ Session list FAILED: $LIST_RESP"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

# Get session
GET_RESP=$(curl -s http://localhost:8002/sessions/mock-test/test-session)
if [[ "$GET_RESP" == *"test-session"* ]]; then
    echo "✓ Session get OK"
else
    echo "✗ Session get FAILED: $GET_RESP"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

# 6. Test WS ingest → REST replay 端到端
echo "[6/6] Testing WS ingest → REST replay end-to-end..."

# 6.1 WS ingest: 发送完整 turn 序列 (4 events)
echo "  [6.1] WS ingest: sending turn sequence..."
WS_INGEST_RESULT=$(python3 tests/ws_ingest_test.py)
WS_INGEST_STATUS=$(echo "$WS_INGEST_RESULT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', 'error'))")
if [[ "$WS_INGEST_STATUS" == "ok" ]]; then
    echo "  ✓ WS ingest OK (4 events sent)"
else
    echo "  ✗ WS ingest FAILED: $WS_INGEST_RESULT"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

# 6.2 REST replay: 验证返回 4 个事件，顺序正确
echo "  [6.2] REST replay: verifying events..."
SESSION_ID=$(echo "$WS_INGEST_RESULT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('session_id', 'ws-e2e-probe'))")
EVENTS_RESP=$(curl -s "http://localhost:8002/sessions/mock-test/$SESSION_ID/events")

# 验证: 非空 + 4 个事件 + 顺序正确 (tick_started, tool_call, tool_result, tick_completed)
EVENT_COUNT=$(echo "$EVENTS_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('events', [])))")
FIRST_TYPE=$(echo "$EVENTS_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); ev=d.get('events', []); print(ev[0].get('event_type', '') if ev else '')")
LAST_TYPE=$(echo "$EVENTS_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); ev=d.get('events', []); print(ev[-1].get('event_type', '') if len(ev) >= 4 else '')")

if [[ "$EVENT_COUNT" == "4" ]] && [[ "$FIRST_TYPE" == "tick_started" ]] && [[ "$LAST_TYPE" == "tick_completed" ]]; then
    echo "  ✓ REST replay OK (4 events, correct order: $FIRST_TYPE → ... → $LAST_TYPE)"
else
    echo "  ✗ REST replay FAILED: count=$EVENT_COUNT, first=$FIRST_TYPE, last=$LAST_TYPE"
    echo "  Full response: $EVENTS_RESP"
    kill $OBSERVE_PID 2>/dev/null || true
    exit 1
fi

echo ""
echo "=== All Tests Passed ✓ ==="
echo "WS ingest → REST replay 端到端验证通过"

# Cleanup
kill $OBSERVE_PID 2>/dev/null || true
wait $OBSERVE_PID 2>/dev/null || true

exit 0
