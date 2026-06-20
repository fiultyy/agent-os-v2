#!/usr/bin/env bash
# 启动 orchestrator 容器(podman)。
# LLM key 从 shell 的 ANTHROPIC_AUTH_TOKEN 读取,绝不硬编码进镜像/脚本。
#
# 用法:
#   bash services/orchestrator/scripts/container-run.sh [HOST_PORT]
#   HOST_PORT 默认 8000(本机服务已停,容器接管 8000;如需避让传别的端口)
#
# 前置: 镜像已构建 —— podman build -t agent-os-orchestrator:latest \
#         -f services/orchestrator/Containerfile services/orchestrator
set -euo pipefail

HOST_PORT="${1:-8000}"
IMAGE="agent-os-orchestrator:latest"
CONTAINER="agent-os-orchestrator"

# bind mount:host orchestrator/data ↔ 容器 /app/data
# 容器与 harness/host 工具共享同一份 db(直接读写 + HTTP API 都走这个目录)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/data"
mkdir -p "$DATA_DIR"

if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "ERROR: ANTHROPIC_AUTH_TOKEN 未设置(智谱 max coding plan key)。" >&2
  echo "请先: export ANTHROPIC_AUTH_TOKEN=<你的智谱 key>" >&2
  exit 1
fi

# 幂等:已存在则先移除
podman rm -f "$CONTAINER" >/dev/null 2>&1 || true

podman run -d --name "$CONTAINER" \
  -p "$HOST_PORT:8000" \
  -v "$DATA_DIR":/app/data \
  -e LLM_API_FORMAT=anthropic \
  -e ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic \
  -e ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" \
  -e LLM_ANTHROPIC_MODEL=glm-5-turbo \
  -e MEMORY_EVENT_BUS_ENABLED=1 \
  -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
  -e http_proxy= -e https_proxy= -e all_proxy= \
  "$IMAGE"

echo ""
echo "✓ 容器已启动: http://127.0.0.1:$HOST_PORT"
echo "  health:       curl http://127.0.0.1:$HOST_PORT/health"
echo "  外部维护接口: POST /v1/memory/notify  |  POST /v1/memory/consolidate"
echo "  日志:         podman logs -f $CONTAINER"
echo "  数据(bind):   $DATA_DIR ↔ /app/data (容器与 harness/host 共享同一 db)"
echo "  停止:         podman rm -f $CONTAINER"
echo "  改端口:       bash scripts/container-run.sh 8002"
