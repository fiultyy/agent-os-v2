.PHONY: dev dev-web dev-gateway dev-orch install build clean

# Install all dependencies
install:
	pnpm install

# Start all services in dev mode
dev:
	docker compose up

# Start frontend only
dev-web:
	cd apps/web && pnpm dev

# Start API Gateway
dev-gateway:
	cd services/gateway && uvicorn src.main:app --reload --port 8000

# Start Orchestrator
dev-orch:
	cd services/orchestrator && uvicorn src.engine:app --reload --port 8001

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

# Generate proto stubs
proto:
	python -m grpc_tools.protoc -I packages/proto --python_out=services/gateway/src/generated --grpc_python_out=services/gateway/src/generated packages/proto/*.proto
