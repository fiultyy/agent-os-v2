# Agent OS

面向 Agent 工作流的操作系统级平台。

## 模块

| 模块 | 说明 |
|------|------|
| **Agent 编排** | 生命周期管理、调度、多 Agent 协同 |
| **Prompt 管理** | 提示词模板引擎、版本控制 |
| **对话观测** | 多轮对话监控、上下文追踪、记忆管理 |
| **资源管理** | Provider 配置、模型路由、负载均衡 |
| **前端画布** | Flow UI — React Flow 可视化节点编辑器 |

## 技术栈

- **前端**: React + React Flow + Zustand + Next.js 15 (App Router)
- **后端**: Python 3.12 微服务 (FastAPI + gRPC)
- **通信**: HTTP/SSE/WebSocket + gRPC
- **数据**: RAG / GraphRAG

## 快速开始

```bash
# 安装依赖
pnpm install

# 启动开发环境
make dev

# 或分别启动
make dev-web      # 前端 :3000
make dev-gateway  # API Gateway :8000
make dev-orch     # 编排服务 :8001
```

## 项目结构

```
apps/web/           → 前端 (Next.js + React Flow)
services/gateway/   → API Gateway (FastAPI)
services/orchestrator/ → Agent 编排服务 (核心)
services/prompt-manager/ → Prompt 管理
services/conversation-observer/ → 对话观测
services/resource-manager/ → 资源管理
packages/shared-types/ → 共享类型定义
packages/proto/     → gRPC protobuf 定义
```

详见 [docs/architecture.md](docs/architecture.md)。

## 开发

见 [docs/contributing.md](docs/contributing.md)。

## License

Private
