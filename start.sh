#!/usr/bin/env bash
set -euo pipefail
# 本地一键启动 orche(:8001)+ observe(:8002)。
# LLM key 走 shell env ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL(orche pydantic-ai AnthropicModel 读);
# 不依赖 .env 的 LLM_API_KEY(旧 schema,orche 不读)。.env 若存在则 source(覆盖默认 port 等),不 cp 占位。

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# miniconda3 sqlite corrupted → preload 系统 libsqlite3(pysqlite3 替代方案见 services/orchestrator/start.py)
if [ -f /lib/x86_64-linux-gnu/libsqlite3.so.0 ]; then
  export LD_PRELOAD="/lib/x86_64-linux-gnu/libsqlite3.so.0${LD_PRELOAD:+:$LD_PRELOAD}"
fi

# .env 可选(不强制、不 cp 占位)
[ -f .env ] && { set -a; source .env; set +a; }

# LLM gate:ANTHROPIC_AUTH_TOKEN(orche 必需)
if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "⚠  ANTHROPIC_AUTH_TOKEN 未设(orche LLM 不可用)。"
  echo "   export ANTHROPIC_AUTH_TOKEN=... ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic"
  exit 1
fi

export ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8001}"
export OBSERVE_PORT="${OBSERVE_PORT:-8002}"
export OBSERVE_URL="${OBSERVE_URL:-http://localhost:${OBSERVE_PORT}}"

PIDS=()
cleanup() { echo ""; echo "stopping..."; for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

echo "→ orche :${ORCHESTRATOR_PORT}"
( cd services/orchestrator && PYTHONPATH=src python -m uvicorn src.engine:app --port "$ORCHESTRATOR_PORT" ) &
PIDS+=($!)
for i in $(seq 1 30); do curl -sf "http://localhost:${ORCHESTRATOR_PORT}/health" >/dev/null 2>&1 && break; sleep 0.5; done

echo "→ observe :${OBSERVE_PORT}"
( cd services/observe && python -m uvicorn src.app:app --port "$OBSERVE_PORT" ) &
PIDS+=($!)
for i in $(seq 1 30); do curl -sf "http://localhost:${OBSERVE_PORT}/health" >/dev/null 2>&1 && break; sleep 0.5; done

echo ""
echo "✅ Backend ready: orche :${ORCHESTRATOR_PORT} / observe :${OBSERVE_PORT}"
echo "→ TUI(另开终端):v2-tui-rs  或  make dev-tui"
echo "   进 TUI 按 4 → Orchestrate tab(fork 树)"
echo "Ctrl+C 停后端。"
wait
