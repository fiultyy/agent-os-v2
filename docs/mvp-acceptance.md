# MVP 验收脚本 — Agent-OS-V2

> **端到端验证 MVP 北极星**:单 agent 对话 + function-calling 工具 + 记忆召回 + 可观测 + 重启不丢。
> 基于 Phase 0/1/2 + function-calling/多轮 loop/PitFail 通电(主线 `feat/frontend-observer-iter`)。
> **运行环境**:容器 `docker compose up`(gateway:8000 + orchestrator:8001 + 真 PG `DATABASE_URL` + LLM auth + 前端 build)。

---

## 前置:启动服务 + 健康检查

```bash
docker compose up -d
curl http://localhost:8000/health            # {"status":"ok"}
curl http://localhost:8000/v1/agents          # 经 gateway /v1 转发(Phase 0 解锁),返回 agent 列表
```

## 本地冒烟(已验证 · 装配证据)

| 检查 | 结果 |
|---|---|
| `from src.engine import app`(pysqlite3+httpx) | ENGINE IMPORT OK |
| 工具 register | **18**(15 primitive + 3 skill) |
| pitfall_registry / memory_service / knowledge_graph / tool_executor | 全装配 |
| orchestrator pytest(排除 e2e 缺 requests) | **635 passed** / 15 baseline failed(环境缺 sentence_transformers + event loop + memory 红线,零新增) |
| gateway pytest | **6 passed**(/v1 前缀转发回归) |
| 前端 `next build` | 绿(L3) |

---

## 五步端到端验收

### Step 1 — 创建 agent(持久化到 PG)

```bash
AGENT=$(curl -s -X POST http://localhost:8000/v1/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"验收助手","model":"glm-4-flash","system_prompt":"你是助手"}')
echo "$AGENT"   # {"id":"<agent_id>", "name":"验收助手", ...}

# 验证 PG 落库(L2 持久化写侧)
docker compose exec postgres psql -U agentos -c "SELECT id,name FROM agents;"
# 预期:含刚创建 agent
```

### Step 2 — 多轮对话 + function-calling 工具

```bash
curl -N -X POST http://localhost:8000/v1/execute \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"<agent_id>","input":"读一下 /etc/hostname 文件内容"}'
```
**预期**:SSE 流 `event: node_start`/`node_complete`;LLM **原生 function-calling** 调 `file_read`(`/etc/hostname`)→ `tool_result` → 若需继续调工具则**多轮 loop** → 最终 `llm_synthesize` 回复(含 hostname 内容)。不再 `web_search not found`(L2 修)+ 不再正则启发式(FC 原生 tool_use)。

### Step 3 — 记忆召回

```bash
curl 'http://localhost:8000/v1/memories?query=hostname&agent_id=<agent_id>&scope=agent&limit=5'
```
**预期**:200,返回对话沉淀的相关记忆(召回排序 f98a1a0,low_info_reply/importance tie-breaker)。

### Step 4 — 前端实时 / 通信 / 历史(浏览器)

打开 `http://localhost:3000`:
- **Canvas Live**(`/canvas/live?session_id=<sid>`):WS 状态"已连接"(L3 /ws rewrite + sessionId 闭环)+ tick/tool 事件流非空
- **Layer2 提交**:进 `layer2.submit` accepted 分支(不再 `unknown cmd`,L3 cmd 协议)
- **CommunicationPanel**:`agent_message` 非空(L2 通信桥 register_delivery_callback → SSE + L4 前端 dispatch)
- **ExecutionHistory**:执行历史非空(node_* 事件)

### Step 5 — 重启不丢

```bash
docker compose restart orchestrator
sleep 5
curl http://localhost:8000/v1/agents | grep 验收助手
```
**预期**:Step 1 创建的 agent 仍在(PostgresStore 持久化 + `restore_agents_from_pg` 启动灌回,L2 双向通电)。

---

## 通过判据

五步全通过 = **MVP 交付**。

## 已知边界(MVP 外 defer,见 mvp-iteration-roadmap.md §5)

蝴蝶翼写侧(记忆红线)/ Checkpoint resume(长任务断点续传)/ ParallelNode 编排(多 Agent)/ 信任域 ScopeManager(通信隔离)/ 辅助服务 observer·rm·pm(归档/通电决策)/ gRPC(100% 未接线)。

## 装配状态总览(MVP boundary_in)

| 模块 | 状态 | 交付 |
|---|---|---|
| gateway→orchestrator /v1 转发 | ✅ | Phase 0(6 测试) |
| orchestrator 工具 register(18)+ chat 默认对齐 + Guardrail dict | ✅ | L2 |
| LLM 原生 function-calling(anthropic+openai) | ✅ | FC `c248894` |
| 多轮 tool_use loop(tool→llm 循环 + tool_result 回注 + max_iter) | ✅ | 多轮 `8cdc48b` |
| Agent 间通信(register_agent + delivery_callback→SSE) | ✅ | L2 |
| Agent 持久化双向(PostgresStore + restore) | ✅ | L2(需真 PG) |
| PitFail 踩坑记录(match/increment + record)+ /v1/pitfall API | ✅ | `92be2cd`(9 测试) |
| 前端实时通路(/ws rewrite + sessionId 闭环 + Layer2 cmd) | ✅ | L3 |
| 前端通信 dispatch(agent_message→CommunicationPanel) | ✅ | L4 |
| 死代码清理(净删 1398 行)+ 文档归位 | ✅ | Phase 2 |
| 记忆召回排序(low_info + importance tie-breaker) | ✅ | `f98a1a0`(已收官) |
