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
│                    Gateway (port 8000)                       │
│  - FastAPI + Uvicorn                                         │
│  - API key Auth → JWT (RS256)                                │
│  - Routes: /auth, /agents, /prompts, /resources, /memories,  │
│    /messages, /debug, /kg, /chat, /execute                   │
│    (均无 /api/v1 根前缀; 前端经 Next.js /api/* → 后端 /v1/*) │
└─────────────────────────────────────────────────────────────┘
          │                                    │
          ▼                                    ▼
┌─────────────────────┐        ┌─────────────────────────────────┐
│ Prompt Manager 8002 │        │    Orchestrator (port 8001)     │
│ - profiles:[aux]    │        │  - Agent Engine (prefix=/v1)     │
│ - Template render   │        │  - MemoryService (SQLite+FAISS) │
│ - Variable inject   │        │  - ToolRegistry                 │
└─────────────────────┘        │  - SkillCatalog                 │
                               │  - ConversationManager           │
                               │  - RuntimeObserverHook (in-proc)│
                               │  - SSE runtime_observation      │
                               │  - /debug/status runtime_anomalies│
                               └─────────────────────────────────┘

注: Conversation Observer 独立服务已于 Phase 0(commit 69179a5)删除,
观测能力内化为 Orchestrator 的 RuntimeObserverHook, 纯内存, 经 SSE
runtime_observation + /debug/status runtime_anomalies 暴露。
ResourceManager 端口 8004 同为 profiles:[aux] 辅助服务(可选启动)。
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
| L1-1 | 启动 Gateway | 进程运行，端口 8000 监听 |
| L1-2 | 启动 Orchestrator | 进程运行，端口 8001 监听 |
| L1-3 | 启动 Prompt Manager(可选, profiles:aux) | 进程运行，端口 8002 监听 |
| L1-4 | (Conversation Observer 已删除, 跳过) | — |
| L1-5 | 主服务并发启动 | gateway(8000)+orchestrator(8001) 2 个主进程正常运行，无端口冲突 |

**验证脚本**:
```bash
# 检查端口监听
ss -tlnp | grep -E '(8000|8001|8002|8004)'
```

### 2.2 L2 - 健康检查验证

| 场景 | 端点 | 预期响应 |
|------|------|----------|
| L2-1 | GET /health (Gateway) | `{"status": "ok"}` |
| L2-2 | GET /health (Orchestrator) | `{"status": "ok"}` |
| L2-3 | GET /health (Prompt Manager) | `{"status": "ok"}` |
| L2-4 | GET /debug/status (Orchestrator) | 返回 runtime_anomalies 数组字段 |

### 2.3 L3 - 认证流程验证

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L3-1 | POST /auth/token (body: {api_key: <pre-shared>}) | 用 pre-shared API key 换 access_token + refresh_token (JWT pair) |
| L3-2 | POST /auth/refresh (body: {refresh_token}) | 刷新，返回新 access_token |
| L3-3 | POST /auth/verify (Authorization: Bearer <access_token>) | 校验 token 有效性 |
| L3-4 | POST /auth/verify (invalid/expired token) | 401 Unauthorized |
| L3-5 | 使用错误 API key 调 /auth/token | 401 Unauthorized |

> 注: 当前 auth 仅 3 个端点(/token, /refresh, /verify), 无 /auth/register、/auth/login、/auth/revoke, 无 /me 端点; Token 用 pre-shared API key 换取, 非 username/password。

**JWT 安全检查**:
- Token 必须 RS256 签名
- Token 包含 user_id, exp, iat, jti
- 撤销列表持久化到 SQLite

### 2.4 L4 - 核心 API 验证

#### 2.4.1 Memory API

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-1 | POST /memories | 创建记忆，返回 memory_id(origin=foreground 默认 P0 保护) |
| L4-2 | GET /memories?agent_id=&session_id=&memory_type=&scope=&query=&sort=importance&limit= | 列表/召回查询，支持过滤;query 非空走 RetrieverAgent → fallback service.recall(无独立 GET-by-id 端点) |
| L4-3 | GET /memories/layers?agent_id= | 获取某 agent 的分层记忆计数 |
| L4-4 | DELETE /memories/{memory_id} | 删除指定记忆 |
| L4-5 | (无独立 /search 端点) | 向量搜索经 GET /memories?query= 触发;cosine 相似度在 orchestrator 内部 memory/vector.py:search() 实现，非 gateway REST 独立端点 |

**MemoryItem 结构**:
```python
{
    "id": "mem_xxx",
    "content": "用户说今天要去开会",
    "agent_id": "agent_001",
    "session_id": "sess_001",
    "memory_type": "session",  # working / session / episodic / semantic
    "scope": "agent",          # agent / session / workspace / global
    "importance": 0.8,
    "metadata": {"source": "user_input"},
    "created_at": "2026-04-17T12:00:00Z"
}
```

#### 2.4.2 Tool API（未实现 / defer）

> ⚠️ HEAD 5fe1bee 无此 API。gateway 路由清单仅 /auth /agents /prompts /resources /memories /messages /debug /kg /chat /execute，无 /tools 路由；orchestrator 亦无 tools REST 路由。工具调用以内部 ToolRegistry（services/orchestrator/src/tools/registry.py）经 LLM function-calling 在 agent turn 内执行，不经 gateway REST CRUD 暴露。下列场景待落地后再启用。

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-6 | GET /api/v1/tools | （defer）返回所有可用工具列表 |
| L4-7 | POST /api/v1/tools/execute | （defer）执行工具，返回结果 |
| L4-8 | POST /api/v1/tools/execute (invalid) | （defer）返回错误信息 |

**Tool 执行测试用例**:
- `datetime` - 返回当前时间
- `web_search` - 搜索测试查询
- `calculator` - 简单数学运算

#### 2.4.3 Skill API（未实现 / defer）

> ⚠️ HEAD 5fe1bee 无此 API。无 /skills 路由；SkillCatalog（services/orchestrator/src/skills/）为内部库，不经 REST 暴露。

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-9 | GET /api/v1/skills | （defer）返回已加载技能列表 |
| L4-10 | POST /api/v1/skills/load | （defer）动态加载新技能 |
| L4-11 | GET /api/v1/skills/{name}/metadata | （defer）获取技能元信息 |

#### 2.4.4 Conversation API（已弃用）

> ⚠️ Conversation 资源(/api/v1/conversations)已在 commit 69179a5 'conversation-observer Phase 0 彻底弃用' 中随 conversation-observer 一并删除, gateway main.py 不再注册 conversations router。当前对话交互经以下端点(均挂载在根路径, 无 /api/v1 前缀):

| 场景 | 操作 | 预期结果 |
|------|------|----------|
| L4-12 | POST /chat | 单 agent 对话(同步) |
| L4-13 | POST /execute | SSE 流式执行(node_start/node_complete/execution_complete 事件) |
| L4-14 | POST /messages | agent 间发消息(代理到 orchestrator) |
| L4-15 | GET /messages | 取消息历史(代理到 orchestrator, 支持 query 过滤) |

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
cd ~/projects/agent-os-v2 && git pull

# 2. 安装依赖
cd services/gateway && pip install -e .
cd services/orchestrator && pip install -e .

# 3. 清理旧数据
rm -rf /tmp/agent-os-v2-test-*
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

> 注: HEAD 5fe1bee 尚未创建 `tests/e2e/` 目录及下列文件, 以下为规划结构。

```
tests/e2e/                    # (规划中, 尚未落地)
├── conftest.py          # pytest fixtures
├── test_l1_startup.py   # L1 进程启动
├── test_l2_health.py    # L2 健康检查
├── test_l3_auth.py      # L3 认证流程
├── test_l4_memory.py    # L4 Memory API
├── test_l5_conversation.py  # L5 端到端对话
└── test_l6_recovery.py  # L6 故障恢复
```

### 3.4 conftest.py 示例

```python
import pytest
import httpx
import asyncio
import os

# 1. 启动所有服务 (docker 或直接进程)
# 2. 等待健康检查通过
# 3. 提供 base_url fixture

@pytest.fixture(scope="session")
def gateway_url():
    return "http://localhost:8000"

@pytest.fixture(scope="session")
def orchestrator_url():
    return "http://localhost:8001"

@pytest.fixture
def auth_token(gateway_url):
    """获取测试用 JWT token (pre-shared API key → JWT pair)"""
    response = httpx.post(f"{gateway_url}/auth/token", json={
        "api_key": os.environ["GATEWAY_API_KEY"]  # pre-shared key
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
| L1 | gateway(8000)+orchestrator(8001) 主进程运行, 可选 aux 服务(8002/8004), 端口正常监听 |
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
cd ~/projects/agent-os-v2

# Gateway
cd services/gateway && python -m uvicorn src.main:app --port 8000 &
# Orchestrator  
cd services/orchestrator && python -m uvicorn src.engine:app --port 8001 &
# Prompt Manager (可选, profiles:aux)
cd services/prompt-manager && python -m uvicorn src.main:app --port 8002 &

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
# 1. 换 token (pre-shared API key → JWT pair)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
    -H "Content-Type: application/json" \
    -d "{\"api_key\":\"$GATEWAY_API_KEY\"}" | jq -r '.access_token')

# 2. 单 agent 对话(同步)
curl -s -X POST http://localhost:8000/chat \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message":"你好"}'

# 3. SSE 流式执行(单 agent 走 /execute;多 agent 编排走 POST /v1/orchestrate)
curl -s -N -X POST http://localhost:8000/execute \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message":"你好"}'
```

---

## 6. 已知限制

| 限制 | 说明 | 解决方向 |
|------|------|----------|
| (历史限制,已消除)真实 LLM 已接入 | glm-4-flash / glm-4.7 经 openai+anthropic 双通道,多 agent 编排 run_agent_turn 直调真实 LLM | — |
| 无消息队列 | 依赖 HTTP 同步调用 | 后续集成 Redis/RabbitMQ |
| 无真实向量数据库 | 使用 FAISS local | 生产环境用 Pinecone/Milvus |
| (历史限制,已消除)前端已完成 | Next.js app router: / 对话+SSE、/agents Agent管理、/memory 4-tab 面板、/canvas、/flows | — |

---

## 7. 后续工作

| 优先级 | 工作项 | 依赖 |
|--------|--------|------|
| P0 | 修复 L4 API 错误 | L4 测试发现的问题 |
| P1 | (已完成)接入真实 LLM Provider | L5 对话测试 |
| P2 | 压力测试 | L5 通过后 |
| P3 | (已完成)前端集成测试 | Next.js 前端已完成 |

---

*验证计划生成时间: 2026-04-17*
