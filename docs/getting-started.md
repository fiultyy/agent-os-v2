> ⚠️ **历史快照(gateway 已退役,2026-07-18)**:本文档含 `:8000` curl 引用指向已删除的 gateway BFF(commit c594969)。现役服务:orchestrator `:8001` / observe `:8002` / native `/h`。文中 `:8000` 示例为失效死引用,不再维护。

# Getting Started

## Prerequisites

- Node.js ≥ 22
- pnpm ≥ 10
- Python ≥ 3.12
- Docker & Docker Compose (optional, for containerized dev)

## Install

```bash
# Clone the repo
git clone <repo-url>
cd agent-os-v2

# Install frontend dependencies
pnpm install

# Install Python service dependencies (core runtime: gateway + orchestrator)
cd services/gateway && pip install -e "."
cd ../orchestrator && pip install -e "."

# prompt-manager(8002) / resource-manager(8004) 为归档辅助服务
# (docker-compose.yml profiles:["aux"])，默认 `docker compose up` 不启动，
# gateway 通过 _aux_call 兜底 502。仅当显式启用 aux profile 时才需要安装：
#   cd ../prompt-manager && pip install -e "."
#   cd ../resource-manager && pip install -e "."
```

## Development

### Option 1: Docker Compose (recommended)

```bash
docker compose up
```

### Option 2: Manual

```bash
# Terminal 1 — Frontend
make dev-web

# Terminal 2 — Gateway
make dev-gateway

# Terminal 3 — Orchestrator
make dev-orch
```

### Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Canvas | http://localhost:3000/canvas |
| Gateway API | http://localhost:8000 |
| Gateway Docs | http://localhost:8000/docs |
| Orchestrator | http://localhost:8001 |

## Project Structure

See [architecture.md](architecture.md) for detailed architecture docs.
