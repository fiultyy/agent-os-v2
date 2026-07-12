#!/usr/bin/env bash
# Smoke test for claude-code-gateway
#
# 启动 observe-service + gateway，运行一个 turn，验证事件序列。
#
# 预期序列: tick_started → tool_call → tool_result → tick_completed

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Claude Code Gateway Smoke Test ==="

# ── 1. 启动 observe-service ──────────────────────────────────────
echo "[1/3] Starting observe-service..."

# 确保日志目录存在
mkdir -p logs

# 检查是否已在运行
if pgrep -f "uvicorn.*app:app" > /dev/null; then
    echo "Observe-service already running, skip start"
else
    python3 -m uvicorn src.app:app --host 0.0.0.0 --port 8002 > logs/observe.log 2>&1 &
    OBSERVE_PID=$!

    # 等待启动
    for i in {1..10}; do
        if curl -s http://localhost:8002/health > /dev/null; then
            echo "✓ Observe-service ready (PID: $OBSERVE_PID)"
            break
        fi
        sleep 0.5
    done
fi

# ── 2. 运行 gateway (claude -p 执行一个 turn) ─────────────────────
echo "[2/3] Running claude-code-gateway..."

SESSION_ID="smoke-$(uuidgen | cut -d'-' -f1)"
PROMPT="用 Bash ls 列出当前目录文件"

timeout 60 python3 gateways/claude_code.py "$SESSION_ID" "$PROMPT" > logs/gateway.log 2>&1
GATEWAY_EXIT=$?

if [ $GATEWAY_EXIT -ne 0 ]; then
    echo "✗ Gateway failed with exit code $GATEWAY_EXIT"
    cat logs/gateway.log
    if [ -n "${OBSERVE_PID:-}" ]; then
        kill $OBSERVE_PID 2>/dev/null || true
    fi
    exit 1
fi

echo "✓ Gateway completed"

# ── 3. 验证 observe-service 收到事件序列 ────────────────────────────
echo "[3/3] Verifying event sequence..."

EVENTS=$(curl -s "http://localhost:8002/sessions/claude-code/$SESSION_ID/events?limit=100")
EVENT_COUNT=$(echo "$EVENTS" | jq '.events | length')

echo "Retrieved $EVENT_COUNT events"

# 检查事件序列
TICK_STARTED=$(echo "$EVENTS" | jq '.events[] | select(.event_type=="tick_started") | length')
TOOL_CALL=$(echo "$EVENTS" | jq '[.events[] | select(.event_type=="tool_call")] | length')
TOOL_RESULT=$(echo "$EVENTS" | jq '[.events[] | select(.event_type=="tool_result")] | length')
TICK_COMPLETED=$(echo "$EVENTS" | jq '.events[] | select(.event_type=="tick_completed") | length')

echo "Event counts:"
echo "  tick_started: $TICK_STARTED"
echo "  tool_call: $TOOL_CALL"
echo "  tool_result: $TOOL_RESULT"
echo "  tick_completed: $TICK_COMPLETED"

# 验证
if [ "$TICK_STARTED" -ge 1 ] && [ "$TOOL_CALL" -ge 1 ] && [ "$TOOL_RESULT" -ge 1 ] && [ "$TICK_COMPLETED" -ge 1 ]; then
    echo "✓ All expected event types present"

    # 打印第一个 tool_call 详情
    echo ""
    echo "Sample tool_call event:"
    echo "$EVENTS" | jq '.events[] | select(.event_type=="tool_call") | {tool_name, arguments}' | head -5

    echo ""
    echo "=== Smoke Test PASSED ==="
    TEST_RESULT=0
else
    echo "✗ Missing expected events"
    echo "Full events:"
    echo "$EVENTS" | jq '.events[] | {event_type, tick_id}'
    TEST_RESULT=1
fi

# ── 清理 ────────────────────────────────────────────────────────────
if [ -n "${OBSERVE_PID:-}" ]; then
    echo "Stopping observe-service..."
    kill $OBSERVE_PID 2>/dev/null || true
fi

# 删除测试 session
curl -s -X DELETE "http://localhost:8002/sessions/claude-code/$SESSION_ID" > /dev/null || true

exit $TEST_RESULT
