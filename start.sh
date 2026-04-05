#!/usr/bin/env bash
set -euo pipefail

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
export GATEWAY_PORT="${GATEWAY_PORT:-8000}"
export ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8001}"
export ORCHESTRATOR_URL="http://localhost:${ORCHESTRATOR_PORT}"

if [ -z "$LLM_API_KEY" ] || [ "$LLM_API_KEY" = "your-api-key-here" ]; then
  echo "⚠  LLM_API_KEY is not set. Open .env and add your API key."
  exit 1
fi

# ── Helper: kill background jobs on exit ────────────────────
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

# Wait for orchestrator to be ready
for i in $(seq 1 20); do
  if curl -sf "http://localhost:${ORCHESTRATOR_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# ── Start gateway ───────────────────────────────────────────
echo "→ Starting gateway on :${GATEWAY_PORT} …"
python -m uvicorn src.main:app \
  --host 0.0.0.0 \
  --port "$GATEWAY_PORT" \
  --app-dir services/gateway \
  &
PIDS+=($!)

# Wait for gateway
for i in $(seq 1 20); do
  if curl -sf "http://localhost:${GATEWAY_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# ── Start frontend ──────────────────────────────────────────
echo "→ Starting frontend …"
cd apps/web
npm run dev &
PIDS+=($!)
cd "$ROOT"

echo ""
echo "✅  All services running!"
echo ""
echo "   Frontend    →  http://localhost:3000"
echo "   Gateway     →  http://localhost:${GATEWAY_PORT}"
echo "   Orchestrator→  http://localhost:${ORCHESTRATOR_PORT}"
echo ""
echo "Press Ctrl+C to stop."

wait
