#!/usr/bin/env bash
set -euo pipefail

# ── Fix miniconda sqlite3 compatibility ─────────────────────
if [ -f /lib/x86_64-linux-gnu/libsqlite3.so.0 ]; then
  export LD_PRELOAD="/lib/x86_64-linux-gnu/libsqlite3.so.0${LD_PRELOAD:+:$LD_PRELOAD}"
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── Load .env ───────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "⚠  .env not found, copying from .env.example …"
  cp .env.example .env
  echo "⚠  Please edit .env and set LLM_API_KEY before running again."
  echo "   Example:  \$EDITOR .env"
  exit 1
fi

set -a; source .env; set +a

export LLM_BASE_URL="${LLM_BASE_URL:-https://open.bigmodel.cn/api/coding/paas/v4}"
export LLM_MODEL="${LLM_MODEL:-glm-4-flash}"
export LLM_API_KEY="${LLM_API_KEY:-}"
export ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8001}"
export OBSERVE_PORT="${OBSERVE_PORT:-8002}"   # TUI 观测层
export ORCHESTRATOR_URL="http://localhost:${ORCHESTRATOR_PORT}"
# orchestrator → observe:容器部署注入 OBSERVE_URL=http://observe:8002(localhost 在容器内解析自身)
export OBSERVE_URL="${OBSERVE_URL:-http://localhost:${OBSERVE_PORT}}"

if [ -z "$LLM_API_KEY" ] || [ "$LLM_API_KEY" = "your-api-key-here" ]; then
  echo "⚠  LLM_API_KEY is not set. Open .env and add your API key."
  exit 1
fi

PIDS=()
cleanup() {
  echo ""
  echo "Stopping services..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null
  echo "Done."
}
trap cleanup EXIT INT TERM

# ── Start orchestrator ──────────────────────────────────────
echo "→ Starting orchestrator on :${ORCHESTRATOR_PORT} …"
python -m uvicorn src.engine:app \
  --host 0.0.0.0 \
  --port "$ORCHESTRATOR_PORT" \
  --app-dir services/orchestrator \
  &
PIDS+=($!)

for i in $(seq 1 20); do
  if curl -sf "http://localhost:${ORCHESTRATOR_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# ── Start observe-service(TUI 观测层,web 弃用后 TUI 接力)──
echo "→ Starting observe-service on :${OBSERVE_PORT} …"
python -m uvicorn src.app:app \
  --host 0.0.0.0 \
  --port "$OBSERVE_PORT" \
  --app-dir services/observe \
  &
PIDS+=($!)

for i in $(seq 1 20); do
  if curl -sf "http://localhost:${OBSERVE_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo ""
echo "✅  Backend services running (web/gateway deprecated → TUI handoff)!"
echo ""
echo "   Orchestrator →  http://localhost:${ORCHESTRATOR_PORT}"
echo "   Observe      →  http://localhost:${OBSERVE_PORT}  (TUI 观测层)"
echo ""
echo "→ TUI 接力前端(另开终端):"
echo "   cargo run --release --manifest-path apps/tui-rs/Cargo.toml"
echo "   (或软链 v2-tui-rs;注意软链指主仓,worktree 改动需 rebuild 主仓)"
echo ""
echo "Press Ctrl+C to stop."

wait
