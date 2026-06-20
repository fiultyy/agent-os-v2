# 记忆系统接入说明（面向外部 Harness）

> 你的应用接入 agent-os-v2 **记忆内核**。本文是对外接入**权威说明**:部署/端口/完整 API/gate/示例。
> API 字段细节见 [`memory-external-api.md`](./memory-external-api.md),记忆内核设计见 [`memory-kernel-design.md`](./memory-kernel-design.md),实施 plan 见 [`memory-kernel-impl-plan.md`](./memory-kernel-impl-plan.md)。

---

## 部署 / 端口

正式容器(podman):
```bash
bash services/orchestrator/scripts/container-run.sh          # 默认 host:8000
bash services/orchestrator/scripts/container-run.sh 8001     # 换端口
```
| 项 | 值 |
|---|---|
| **端口** | `8000`(host)→ 8000(容器) |
| **base URL** | `http://127.0.0.1:8000` |
| **LLM** | glm-5-turbo(anthropic 端点,复用 claude code 额度,稳定) |
| **data** | bind mount `services/orchestrator/data/` ↔ 容器 `/app/data`(`memories.db` / `kg.db` / `neural_field.db`) |
| **gate** | 全默认**关** → 生产零回归(确定性管线);按需开 gate 启用记忆内核 |

---

## 完整 API（外部 harness 调用）

### 写入 `POST /v1/memories`
```json
{"content":"...", "agent_id":"", "session_id":"", "memory_type":"session",
 "scope":"agent", "importance":0.5, "sync_extract":false}
```
- `sync_extract=true`(记忆内核 ①):**同步等 IngestorAgent LLM 抽取** → 返回 `entities_added`/`relations_added`/`importance`(五维)/`identity_category`/`degraded`。需 `MEMORY_INGESTOR_ENABLED=1`。
- `sync_extract=false`(默认):fire-and-forget,立即返回。

### 召回 `GET /v1/memories`
```
GET /v1/memories?query=FastAPI&sort=importance&agent_id=&memory_type=&limit=100
```
- `query=`(记忆内核 ③):**语义召回**,走 RetrieverAgent(`match×lif_weight`),返回**带 score 排序**结果。需 `MEMORY_RETRIEVER_ENABLED=1`;关则 fallback `service.recall`。
- `sort=importance`:按 importance desc。
- 无 `query`:全量列表(按 created_at)。

### 身份四问(记忆内核 ⑧) `GET /v1/identity`
```
GET /v1/identity?agent_id=
```
- 返回四分组:`what_i_remember` / `who_am_i` / `my_goals` / `my_traits` + `personality`(LIF baseline 慢变量人格)
- 依赖 ① 的 `identity_category` 标签(IDENTITY/GOAL/TRAIT/KNOWLEDGE)
- **程序化输出**(记忆群 + 分数),LLM 综合涌现是**请求方**事(本端点不综合)

### 维护 `POST /v1/memory/notify`
```json
{"agent_id":"", "force":false, "curate":false}
```
- 零 LLM 确定性维护(prune/forget/migrate)
- `curate=true`(记忆内核 ④):额外触发 **CuratorAgent LLM 策展**(独立 fire-and-forget task,非同步链)。需 `MEMORY_CURATOR_ENABLED=1`。

### 巩固 `POST /v1/memory/consolidate`
```json
{"agent_id":"", "session_id":"", "messages":[...], "mode":"task_consolidator", "timeout":8.0}
```
- `mode=task_consolidator`(默认):任务后经验提取(BackwardWriter 三通道)
- `mode=merge`(记忆内核 ②):**ConsolidatorAgent** episodic→semantic LLM 合并。需 `MEMORY_CONSOLIDATOR_ENABLED=1`。
- timeout clamp 8s;空 `messages` 拒绝;LLM 未配 no-op;失败降级启发式

### 其他
- `GET /v1/agents` → agent 列表(拿 `agent_id`)
- `GET /v1/memories/layers` → 层级统计
- `DELETE /v1/memories/{id}` → 删记忆

---

## 记忆内核 5 gate（启用智能能力）

正式容器默认全关(确定性管线 = P0~P3 已验证的机械记忆管理)。开 gate 启用记忆内核(⑤ 神经状态场需 TURN_END 触发,走 `/chat`/`/execute`):

| gate | 启用 | 灰度验证 |
|---|---|---|
| `MEMORY_EVENT_BUS_ENABLED=1` | 事件总线(其他 gate 前置) | ✅ |
| `MEMORY_INGESTOR_ENABLED=1` | ① IngestorAgent(LLM 抽 KG + 五维 + identity) | ✅ e2e(glm-5-turbo 6 实体/identity) |
| `MEMORY_RETRIEVER_ENABLED=1` | ③ RetrieverAgent(召回 match×lif) | ✅ e2e(query+score) |
| `MEMORY_CONSOLIDATOR_ENABLED=1` | ② ConsolidatorAgent(episodic→semantic) | 单测✅(e2e 需 agent episodic 数据) |
| `MEMORY_CURATOR_ENABLED=1` | ④ CuratorAgent(LLM 策展) | 触发✅(LLM 待 agent 数据) |
| `MEMORY_NEURAL_FIELD_ENABLED=1` | ⑤ NeuralField(神经状态场漂移) | ✅ e2e(漂移收敛+快照+异常) |

**开 gate 示例**(在 `container-run.sh` 或 `podman run` 加 `-e`):
```bash
-e MEMORY_EVENT_BUS_ENABLED=1 \
-e MEMORY_INGESTOR_ENABLED=1 \
-e MEMORY_RETRIEVER_ENABLED=1 \
-e MEMORY_CONSOLIDATOR_ENABLED=1 \
-e MEMORY_CURATOR_ENABLED=1 \
-e MEMORY_NEURAL_FIELD_ENABLED=1
```

---

## 三种接入方式

### A — 只共享 db(被动,零 API)
直写 `memories` 表 → 60s 轮询检测 → 确定性维护(只动 `origin=agent`)。
- `foreground` 写入靠 `recall(query=)` 被动可见,**不被自动整理**(P0 保护)
- 延迟 ≤60s

### B — API 写入 + 召回(**推荐**)
`POST /v1/memories`(sync_extract=true 抽 KG + identity 标签) + `GET /v1/memories?query=`(语义召回) + `GET /v1/identity`(身份四问)。
- 走 API(服务逻辑/校验/事件),harness **不碰 db 文件**
- 需开 `INGESTOR` + `RETRIEVER` gate(+ `EVENT_BUS`)

### C — LLM 巩固
`POST /v1/memory/consolidate`(mode=merge 合并 / 默认经验提取)。
- LLM 成本,8s timeout,降级兜底

---

## updated_at 约定（直写 db 必须,方式 A）
tz-aware ISO 8601 UTC,与 orchestrator 一致:
```python
from datetime import datetime, timezone
updated_at = datetime.now(timezone.utc).isoformat()   # '2026-06-20T10:00:00.123456+00:00' ✓
# '2026-06-20T10:00:00'      ✗ 无时区 → 轮询视为远古,不检测
# '2026-06-20T12:00:00+02:00' ✗ 非UTC → 比较错乱
```
格式错 → 轮询**检测不到**(方式 A 失效)。

---

## 最小示例（Python:API 写入 + 召回 + 身份）

```python
import requests
ORCH = "http://127.0.0.1:8000"
AGENT = requests.get(f"{ORCH}/v1/agents").json()[0]["id"]

# ① 写入 + IngestorAgent LLM 抽取(需 INGESTOR gate)
r = requests.post(f"{ORCH}/v1/memories", json={
    "content": "我用 FastAPI 部署 PostgreSQL,我的目标是构建可靠 Python 后端",
    "agent_id": AGENT, "memory_type": "semantic", "scope": "agent",
    "importance": 0.8, "sync_extract": True,
})
print(r.json())
# {"id":"...", "entities_added":6, "relations_added":4, "importance":0.7375,
#  "identity_category":"IDENTITY", "degraded":false}

# ③ 语义召回(需 RETRIEVER gate)
r = requests.get(f"{ORCH}/v1/memories",
                 params={"query": "FastAPI", "sort": "importance", "limit": 5})
print(r.json())   # [{"id":..., "content":..., "importance":..., "score":1.0}, ...]

# ⑧ 身份四问
r = requests.get(f"{ORCH}/v1/identity", params={"agent_id": AGENT})
print(r.json())
# {"who_am_i":[...], "my_goals":[...], "my_traits":[...], "what_i_remember":[...], "personality":{...}}
```

---

## 约束清单

| # | 约束 | 后果 |
|---|---|---|
| 1 | 直写 db 时 `updated_at` 必须 tz ISO UTC | 格式错 → 轮询检测不到 |
| 2 | `origin=foreground` 记忆**永不被自动归档/迁移/合并**(P0) | 只能 recall 被动读,或 `/consolidate` 产新 agent 记忆 |
| 3 | `/consolidate` **无 rate limit** | 高频调用累积 LLM 成本,调用方限流 |
| 4 | 单 replica 假设 | 多 replica 需额外 SQLite 锁 / migrate 幂等 |
| 5 | gate 默认关 → 记忆内核不启用 | 要 ①②③④⑤ 能力必须显式开 gate |
| 6 | `agent_id` 要一致 | 写入/召回/身份用同一 agent_id,否则召回不到 |

## 故障排查

| 现象 | 排查 |
|---|---|
| `sync_extract` 返回 `degraded:true` | LLM 超时/失败 → 降级 regex。配 glm-5-turbo(anthropic);Ingestor timeout 20s |
| `sync_extract` 返回 `identity_category:NONE` | 降级(regex 无标签)或 LLM 未配。查 `MEMORY_INGESTOR_ENABLED=1` |
| `GET ?query=` 无 score 排序 | RETRIEVER gate 关 → fallback service.recall(无 score)。开 `MEMORY_RETRIEVER_ENABLED=1` |
| `GET /identity` 四问空 | ① Ingestor 未打 identity_category 标签,或 agent_id 不匹配(写入/查询用同一 agent_id) |
| `mode=merge` 无合并产物 | 无 `origin=agent` episodic 候选(POST 写 foreground)。需系统产 agent 记忆(task_consolidator/dreamer) |
| `curate=true` 无策展 | CURATOR gate 关,或无 agent 候选。查日志 `podman logs agent-os-orchestrator \| grep -i curat` |
| LLM 429 余额不足 | glm-4.5-air/4.7 在 OpenAI 端点无额度。用 **anthropic 端点 + glm-5-turbo**(claude code 额度) |
| 我写的记忆没召回 | 查 `scope`/`archived`/`agent_id` 过滤;recall 每轮重查 |

---

## 相关文档
- [`memory-external-api.md`](./memory-external-api.md) — API 字段细节 / SSE 事件 / 失败模式
- [`memory-kernel-design.md`](./memory-kernel-design.md) — 记忆内核设计(扩散激活/神经状态场/身份闭环)
- [`memory-kernel-impl-plan.md`](./memory-kernel-impl-plan.md) — 实施 plan + 审查修正
- [`memory-data-flow.md`](./memory-data-flow.md) — 数据操作完整 flow
