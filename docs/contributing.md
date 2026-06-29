# Contributing

## Development Workflow

1. Create a feature branch from `main`
2. Make changes
3. Ensure lint passes: `make lint`
4. Submit PR

## Code Style

- **TypeScript/React**: Strict mode, ESLint defaults
- **Python**: Black formatting, type hints required
- **Proto**: Follow Google proto3 style guide

## Commit Convention

```
type(scope): description

feat(orchestrator): add context compiler skeleton
fix(gateway): handle missing orchestrator connection
docs: update architecture diagram
```

## Architecture

See [architecture.md](architecture.md) for the full architecture overview.

## Module Ownership

| Module | Path | Description |
|--------|------|-------------|
| Frontend | `apps/web/` | Next.js + @xyflow/react + Zustand |
| Gateway | `services/gateway/` | API Gateway (FastAPI) |
| Orchestrator | `services/orchestrator/` | Core orchestration engine |
| Prompt Manager | `services/prompt-manager/` | Prompt templates & versions |
| Resource Manager | `services/resource-manager/` | Provider adapters & routing |
| Protobuf | `packages/proto/` | gRPC service definitions |
