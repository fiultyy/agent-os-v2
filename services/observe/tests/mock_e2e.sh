#!/bin/bash
# Mock Gateway E2E Test: session 切换隔离验证
#
# 验证:
# 1. 两个 mock gateway (claude-code + openclaw) 发送事件
# 2. observe-service 正确 replay 各自的事件序列
# 3. session 隔离 (互不串事件)
# 4. 连跑两次 (防累积 bug)

set -e

# ── 配置 ─────────────────────────────────────────────────────
OBSERVE_URL="http://localhost:8002"
PROJECT_ROOT="/home/yy/projects/agent-os-v2"
GATEWAY_DIR="$PROJECT_ROOT/services/observe/gateways"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ── 辅助函数 ─────────────────────────────────────────────────────

# 检查 observe-service 是否运行
check_observe_service() {
    if ! curl -s "$OBSERVE_URL/health" | grep -q "ok"; then
        log_error "Observe-service not running at $OBSERVE_URL"
        log_info "Start it with: cd $PROJECT_ROOT/services/observe && bash start.sh"
        exit 1
    fi
    log_info "Observe-service is running"
}

# 生成唯一 session_id
generate_session_id() {
    python3 -c "import uuid; print(uuid.uuid4())"
}

# 运行 mock gateway
run_mock_gateway() {
    local script=$1
    local session_id=$2
    local harness_id=$3

    log_info "Running $script (session=$session_id, harness=$harness_id)"
    python3 "$GATEWAY_DIR/$script" --session-id "$session_id" --harness-id "$harness_id"
}

# 获取 session 事件
get_session_events() {
    local harness_type=$1
    local session_id=$2

    curl -s "$OBSERVE_URL/sessions/$harness_type/$session_id/events"
}

# 验证事件序列
validate_event_sequence() {
    local events_json=$1
    local harness_type=$2
    local session_id=$3

    # 检查事件数量（应该是 4 个：tick_started, tool_call, tool_result, tick_completed）
    local event_count=$(echo "$events_json" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('events', [])))")

    if [ "$event_count" -ne 4 ]; then
        log_error "Expected 4 events for $harness_type/$session_id, got $event_count"
        echo "$events_json" | python3 -m json.tool
        return 1
    fi

    # 检查事件类型顺序
    local expected_types='["tick_started", "tool_call", "tool_result", "tick_completed"]'
    local actual_types=$(echo "$events_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
types = [e['event_type'] for e in data.get('events', [])]
print(json.dumps(types))
")

    if [ "$actual_types" != "$expected_types" ]; then
        log_error "Event type sequence mismatch for $harness_type/$session_id"
        log_error "Expected: $expected_types"
        log_error "Actual:   $actual_types"
        return 1
    fi

    # 检查 session_id 一致性（所有事件的 session_id 应该相同）
    local session_check=$(echo "$events_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
events = data.get('events', [])
session_ids = set(e['session_id'] for e in events)
print('OK' if len(session_ids) == 1 and session_ids.pop() == '$session_id' else 'MISMATCH')
")

    if [ "$session_check" != "OK" ]; then
        log_error "Session ID mismatch in events for $harness_type/$session_id"
        return 1
    fi

    # 检查 harness_type 一致性
    local harness_check=$(echo "$events_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
events = data.get('events', [])
harness_types = set(e['harness_type'] for e in events)
print('OK' if len(harness_types) == 1 and harness_types.pop() == '$harness_type' else 'MISMATCH')
")

    if [ "$harness_check" != "OK" ]; then
        log_error "Harness type mismatch in events for $harness_type/$session_id"
        return 1
    fi

    log_info "✓ Event sequence validation passed for $harness_type/$session_id ($event_count events, correct order)"
    return 0
}

# 验证 session 隔离（互不串事件）
validate_session_isolation() {
    local claude_events=$1
    local openclaw_events=$2
    local claude_sid=$3
    local openclaw_sid=$4

    # 检查 claude-code 事件是否都是 claude-code 的 session
    local claude_isolation=$(echo "$claude_events" | python3 -c "
import sys, json
data = json.load(sys.stdin)
events = data.get('events', [])
is_isolated = all(e['session_id'] == '$claude_sid' and e['harness_type'] == 'claude-code' for e in events)
print('OK' if is_isolated else 'LEAK')
")

    if [ "$claude_isolation" != "OK" ]; then
        log_error "Claude-code session isolation violated! Events leaked from other session."
        return 1
    fi

    # 检查 openclaw 事件是否都是 openclaw 的 session
    local openclaw_isolation=$(echo "$openclaw_events" | python3 -c "
import sys, json
data = json.load(sys.stdin)
events = data.get('events', [])
is_isolated = all(e['session_id'] == '$openclaw_sid' and e['harness_type'] == 'openclaw' for e in events)
print('OK' if is_isolated else 'LEAK')
")

    if [ "$openclaw_isolation" != "OK" ]; then
        log_error "Openclaw session isolation violated! Events leaked from other session."
        return 1
    fi

    log_info "✓ Session isolation validated (no event leakage between sessions)"
    return 0
}

# ── 主测试流程 ─────────────────────────────────────────────────────

run_test_round() {
    local round=$1

    log_info "========================================"
    log_info "Starting Test Round $round"
    log_info "========================================"

    # 生成唯一 session_id
    CLAUDE_SESSION=$(generate_session_id)
    OPENCLAW_SESSION=$(generate_session_id)

    log_info "Generated session IDs:"
    log_info "  claude-code: $CLAUDE_SESSION"
    log_info "  openclaw:     $OPENCLAW_SESSION"

    # 启动两个 mock gateway（串行启动，避免竞争）
    log_info "Starting mock gateways..."

    run_mock_gateway "mock_claude_code.py" "$CLAUDE_SESSION" "mock-claude-code-1"
    log_info "✓ Mock claude-code gateway completed"

    run_mock_gateway "mock_openclaw.py" "$OPENCLAW_SESSION" "mock-openclaw-1"
    log_info "✓ Mock openclaw gateway completed"

    # 短暂等待，确保 observe-service 处理完所有事件
    sleep 2

    # 获取两个 session 的事件
    log_info "Fetching events from observe-service..."

    CLAUDE_EVENTS=$(get_session_events "claude-code" "$CLAUDE_SESSION")
    OPENCLAW_EVENTS=$(get_session_events "openclaw" "$OPENCLAW_SESSION")

    # 验证事件序列
    log_info "Validating event sequences..."

    if ! validate_event_sequence "$CLAUDE_EVENTS" "claude-code" "$CLAUDE_SESSION"; then
        log_error "Claude-code event sequence validation failed"
        return 1
    fi

    if ! validate_event_sequence "$OPENCLAW_EVENTS" "openclaw" "$OPENCLAW_SESSION"; then
        log_error "Openclaw event sequence validation failed"
        return 1
    fi

    # 验证 session 隔离
    log_info "Validating session isolation..."

    if ! validate_session_isolation "$CLAUDE_EVENTS" "$OPENCLAW_EVENTS" "$CLAUDE_SESSION" "$OPENCLAW_SESSION"; then
        log_error "Session isolation validation failed"
        return 1
    fi

    log_info "✓ Test Round $round PASSED"

    # 输出事件摘要（可选）
    log_info "Event summary:"
    echo "$CLAUDE_EVENTS" | python3 -c 'import sys, json; data = json.load(sys.stdin); events = data.get("events", []); print("  claude-code: {} events".format(len(events))); [print("    - {} (tick_id={}...)".format(e["event_type"], e["tick_id"][:8])) for e in events]'

    echo "$OPENCLAW_EVENTS" | python3 -c 'import sys, json; data = json.load(sys.stdin); events = data.get("events", []); print("  openclaw: {} events".format(len(events))); [print("    - {} (tick_id={}...)".format(e["event_type"], e["tick_id"][:8])) for e in events]'

    return 0
}

# ── 执行测试 ─────────────────────────────────────────────────────

main() {
    log_info "========================================"
    log_info "Mock Gateway E2E Test"
    log_info "========================================"

    # 检查 observe-service
    check_observe_service

    # 连跑两次（防累积 bug）
    for round in 1 2; do
        if ! run_test_round $round; then
            log_error "Test round $round FAILED"
            exit 1
        fi

        # Round 之间短暂等待
        if [ $round -lt 2 ]; then
            sleep 1
        fi
    done

    log_info "========================================"
    log_info "✓✓✓ ALL TESTS PASSED ✓✓✓"
    log_info "Session switch isolation verified (2 rounds)"
    log_info "========================================"

    # 输出成功信号
    echo ""
    echo "session switch ok"
    echo ""

    return 0
}

# 运行主测试
main
