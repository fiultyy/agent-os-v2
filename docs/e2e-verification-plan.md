# Agent OS E2E 完整验证计划

> **版本**: v1.0  
> **日期**: 2026-04-17  
> **目的**: 验证 Agent OS 全栈端到端功能

---

## 1. 验证架构

### 1.1 服务拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                        Client (curl / test)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Gateway (port 8100)                       │
│  - FastAPI + Uvicorn                                         │
│  - JWT Auth (RS256)                                          │
│  - Routes: /auth/*, /api/v1/*                               │
└─────────────────────────────────────────────────────────────┘
          │                                    │
          ▼                                    ▼
┌─────────────────────┐        ┌─────────────────────────────────┐
│ Prompt Manager 8102  │        │    Orchestrator (port 8101)     │
│ - Template render   │        │  - Agent Engine                 │
│ - Variable inject   │        │  - MemoryService (SQLite+FAISS) │
└─────────────────────┘        │  - ToolRegistry                 │
                               │  - SkillCatalog                 │
                               │  - ConversationManager           │
                               └─────────────────────────────────┘
                                          │
                                          ▼
                               ┌─────────────────────────────────┐
                               │ Conversation Observer (port 8103)│
                               │ - Event capture                 │
                               │ - Metrics                       │
                               └─────────────────────────────────┘
```

### 1.2 验证层次

| 层次 | 范围 | 目标 |
|------|------|------|
| **L1** | 进程启动 | 所有服务能独立启动 |
| **L2** | 健康检查 | /health 端点响应 |
| **L3** | 认证流程 | JWT 颁发→使用→刷新→撤销 |
| **L4** | 核心 API | Memory CRUD、Tool Call、Skill 加载 |
| **L5** | 端到端对话 | 完整的多轮对话 + 记忆持久化 |
| **L6** | 故障恢复 | 服务重启后状态恢复 |

---

## 2. 验证场景矩阵

### 2.1 L1 - 进程启动验证

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L1-1 | 启动 Gateway | 进程运行，端口 8100 监听 |
| L1-2 | 启动 Orchestrator | 进程运行，端口 8101 监听 |
| L1-3 | 启动 Prompt Manager | 进程运行，端口 8102 监听 |
| L1-4 | 启动 Conversation Observer | 进程运行，端口 8103 监听 |
| L1-5 | 全部服务并发启动 | 5 个进程正常运行，无端口冲突 |

**验证脚本**:
```bash
# 检查端口监听
ss -tlnp | grep -E '(8100|8001|8002|8103)'
```

### 2.2 L2 - 健康检查验证

| 场景 | 端点 | 预期响应 |
|------|------|----------|
| L2-1 | GET /health (Gateway) | `{"status": "ok"}` |
| L2-2 | GET /health (Orchestrator) | `{"status": "ok"}` |
| L2-3 | GET /health (Prompt Manager) | `{"status": "ok"}` |
| L2-4 | GET /health (Conv Observer) | `{"status": "ok"}` |

### 2.3 L3 - 认证流程验证

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L3-1 | POST /auth/register | 创建用户，返回 user_id |
| L3-2 | POST /auth/login | 返回 access_token + refresh_token |
| L3-3 | GET /api/v1/me (with token) | 返回当前用户信息 |
| L3-4 | GET /api/v1/me (no token) | 401 Unauthorized |
| L3-5 | POST /auth/refresh | 使用 refresh_token 刷新 |
| L3-6 | POST /auth/revoke | 撤销 token，黑名单验证 |
| L3-7 | 使用已撤销 token | 401 Unauthorized |

**JWT 安全检查**:
- Token 必须 RS256 签名
- Token 包含 user_id, exp, iat, jti
- 撤销列表持久化到 SQLite

### 2.4 L4 - 核心 API 验证

#### 2.4.1 Memory API

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-1 | POST /api/v1/memories | 创建记忆，返回 memory_id |
| L4-2 | GET /api/v1/memories/{id} | 获取指定记忆 |
| L4-3 | GET /api/v1/memories?scope=agent | 列表查询，支持过滤 |
| L4-4 | DELETE /api/v1/memories/{id} | 删除记忆 |
| L4-5 | POST /api/v1/memories/search | 向量搜索，返回相关记忆 |

**MemoryItem 结构**:
```python
{
    "id": "mem_xxx",
    "content": "用户说今天要去开会",
    "agent_id": "agent_001",
    "session_id": "sess_001",
    "memory_type": "SESSION",  # SESSION / EPISODIC / SEMANTIC
    "scope": "AGENT",          # AGENT / SESSION / GLOBAL
    "importance": 0.8,
    "metadata": {"source": "user_input"},
    "created_at": "2026-04-17T12:00:00Z"
}
```

#### 2.4.2 Tool API

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-6 | GET /api/v1/tools | 返回所有可用工具列表 |
| L4-7 | POST /api/v1/tools/execute | 执行工具，返回结果 |
| L4-8 | POST /api/v1/tools/execute (invalid) | 返回错误信息 |

**Tool 执行测试用例**:
- `datetime` - 返回当前时间
- `web_search` - 搜索测试查询
- `calculator` - 简单数学运算

#### 2.4.3 Skill API

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-9 | GET /api/v1/skills | 返回已加载技能列表 |
| L4-10 | POST /api/v1/skills/load | 动态加载新技能 |
| L4-11 | GET /api/v1/skills/{name}/metadata | 获取技能元信息 |

#### 2.4.4 Conversation API

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-12 | POST /api/v1/conversations | 创建新对话 |
| L4-13 | GET /api/v1/conversations/{id} | 获取对话详情 |
| L4-14 | POST /api/v1/conversations/{id}/messages | 发送消息 |
| L4-15 | GET /api/v1/conversations/{id}/messages | 获取历史消息 |

### 2.5 L5 - 端到端对话验证

#### 场景 L5-1: 完整多轮对话

**流程**:
1. 用户登录 → 获取 token
2. 创建新对话
3. 发送第 1 条消息 "你好，我叫张三"
4. 验证回复
5. 发送第 2 条消息 "我刚才说我叫什么？"
6. 验证 Agent 能回忆上下文
7. 发送第 3 条消息 "今天天气怎么样？" (触发工具)
8. 验证工具调用结果

**预期**:
- Agent 能记住用户名字 "张三"
- 对话历史完整保存
- 工具调用成功

#### 场景 L5-2: 记忆跨会话持久化

**流程**:
1. 用户 A 创建对话，存入重要记忆 "用户喜欢喝咖啡"
2. 结束对话
3. 1 小时后新对话
4. 询问 "我有什么偏好？"
5. 验证 Agent 能从记忆中检索到偏好

### 2.6 L6 - 故障恢复验证

#### 场景 L6-1: Orchestrator 重启恢复

**流程**:
1. L5-1 完整对话后，记录所有 memory_id
2. 强制 kill Orchestrator 进程
3. 重启 Orchestrator
4. 使用相同 session_id 发送新消息
5. 验证历史记忆仍可访问

**预期**:
- SQLite 数据完整保留
- FAISS 索引完整保留
- 新请求能正常处理

#### 场景 L6-2: Gateway 重启

**流程**:
1. 获取 token
2. 重启 Gateway
3. 使用相同 token 继续请求

**预期**:
- Token 仍有效
- JWT 黑名单持久化仍生效

---

## 3. 验证执行计划

### 3.1 前置条件

```bash
# 1. 确保代码最新
cd ~/projects/agent-os && git pull

# 2. 安装依赖
cd services/gateway && pip install -e .
cd services/orchestrator && pip install -e .

# 3. 清理旧数据
rm -rf /tmp/agent-os-test-*
```

### 3.2 执行顺序

```
Phase 1: 基础设施验证 (L1-L2)
    ↓
Phase 2: 认证流程验证 (L3)
    ↓
Phase 3: 核心 API 验证 (L4)
    ↓
Phase 4: 端到端场景验证 (L5)
    ↓
Phase 5: 故障恢复验证 (L6)
```

### 3.3 测试脚本模板

**测试入口**: `tests/e2e/`

```
tests/e2e/
├── conftest.py          # pytest fixtures
├── test_l1_startup.py   # L1 进程启动
├── test_l2_health.py    # L2 健康检查
├── test_l3_auth.py      # L3 认证流程
├── test_l4_memory.py    # L4 Memory API
├── test_l4_tools.py     # L4 Tool API
├── test_l4_skills.py    # L4 Skill API
├── test_l5_conversation.py  # L5 端到端对话
└── test_l6_recovery.py  # L6 故障恢复
```

### 3.4 conftest.py 示例

```python
import pytest
import httpx
import asyncio

# 1. 启动所有服务 (docker 或直接进程)
# 2. 等待健康检查通过
# 3. 提供 base_url fixture

@pytest.fixture(scope="session")
def gateway_url():
    return "http://localhost:8100"

@pytest.fixture(scope="session")
def orchestrator_url():
    return "http://localhost:8101"

@pytest.fixture
def auth_token(gateway_url):
    """获取测试用 JWT token"""
    response = httpx.post(f"{gateway_url}/auth/login", json={
        "username": "test_user",
        "password": "test_password"
    })
    return response.json()["access_token"]

@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}
```

---

## 4. 验收标准

### 4.1 通过标准

| 层次 | 通过条件 |
|------|----------|
| L1 | 所有 5 个服务进程运行，端口正常监听 |
| L2 | 所有 /health 端点返回 200 |
| L3 | JWT 全流程（创建→使用→刷新→撤销）正常 |
| L4 | 所有 API 场景通过，无 5xx 错误 |
| L5 | 多轮对话成功，记忆正确检索 |
| L6 | 重启后数据完整，服务自动恢复 |

### 4.2 失败处理

| 失败场景 | 处理方式 |
|----------|----------|
| 端口冲突 | 检查是否有旧进程占用 |
| 服务启动失败 | 查看日志定位问题 |
| API 返回 5xx | 查看 Orchestrator 日志 |
| 记忆检索失败 | 检查 SQLite + FAISS 数据 |
| Token 失效 | 检查 JWT 签名密钥配置 |

---

## 5. 快速验证命令

### 5.1 一键启动所有服务

```bash
cd ~/projects/agent-os

# Gateway
cd services/gateway && python -m uvicorn main:app --port 8100 &
# Orchestrator  
cd services/orchestrator && python -m uvicorn main:app --port 8101 &
# Prompt Manager
cd services/prompt-manager && python -m uvicorn main:app --port 8102 &

sleep 3
```

### 5.2 健康检查

```bash
for port in 8000 8001 8002; do
    echo "Port $port: $(curl -s http://localhost:$port/health)"
done
```

### 5.3 完整 e2e 流程测试

```bash
# 1. 登录
TOKEN=$(curl -s -X POST http://localhost:8100/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"test","password":"test"}' | jq -r '.access_token')

# 2. 创建对话
CONV_ID=$(curl -s -X POST http://localhost:8100/api/v1/conversations \
    -H "Authorization: Bearer $TOKEN" | jq -r '.id')

# 3. 发送消息
curl -s -X POST "http://localhost:8100/api/v1/conversations/$CONV_ID/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"content":"你好"}'
```

---

## 6. 已知限制

| 限制 | 说明 | 解决方向 |
|------|------|----------|
| 无真实 LLM Provider | 测试使用 mock | 接入 zhipu/glm 或 anthropic |
| 无消息队列 | 依赖 HTTP 同步调用 | 后续集成 Redis/RabbitMQ |
| 无真实向量数据库 | 使用 FAISS local | 生产环境用 Pinecone/Milvus |
| 前端未完成 | 只有 API | V2 计划开发 React Flow UI |

---

## 7. 后续工作

| 优先级 | 工作项 | 依赖 |
|--------|--------|------|
| P0 | 修复 L4 API 错误 | L4 测试发现的问题 |
| P1 | 接入真实 LLM Provider | L5 对话测试 |
| P2 | 压力测试 | L5 通过后 |
| P3 | 前端集成测试 | React Flow UI 完成 |

---

*验证计划生成时间: 2026-04-17*
