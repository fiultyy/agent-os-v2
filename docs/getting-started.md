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
cd agent-os

# Install frontend dependencies
pnpm install

# Install Python service dependencies
cd services/gateway && pip install -e "."
cd ../orchestrator && pip install -e "."
cd ../prompt-manager && pip install -e "."
cd ../conversation-observer && pip install -e "."
cd ../resource-manager && pip install -e "."
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
