.PHONY: dev dev-orch dev-observe dev-tui install build clean lint

# Install all dependencies
install:
	pnpm install

# Start all services in dev mode (docker compose)
dev:
	docker compose up

# Start Orchestrator (native /h 路径 + 多 agent + memory)
dev-orch:
	cd services/orchestrator && PYTHONPATH=src LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0 \
		uvicorn src.engine:app --reload --port 8001
	# PYTHONPATH=src: a2a 是 top-level import(agent_registry from a2a.card),需 src 在 path
	# LD_PRELOAD: miniconda3 sqlite corrupted,preload 系统 libsqlite3

# Start observe-service (TUI 观测层)
dev-observe:
	cd services/observe && LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0 \
		uvicorn src.app:app --reload --port 8002

# Start TUI (接力前端,Rust ratatui)
dev-tui:
	cargo run --release --manifest-path apps/tui-rs/Cargo.toml

# Build all
build:
	pnpm build

# Clean build artifacts
clean:
	pnpm clean
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".turbo" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".next" -exec rm -rf {} + 2>/dev/null || true

# Lint
lint:
	pnpm lint
