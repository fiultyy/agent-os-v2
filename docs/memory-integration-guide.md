# 记忆系统接入说明（面向外部 Harness）

> 你的应用要和 agent-os-v2 **共享记忆库**？本指南给出 3 种接入方式 + 最小示例 + 约束清单。完整 API 字段见 [`memory-external-api.md`](./memory-external-api.md)。

## 前置：共享什么

orchestrator 的记忆库存于 `services/orchestrator/data/memories.db`（SQLite，WAL 模式，支持并发读写）。你的 harness 可直接读写这个文件。关键字段：

| 字段 | 含义 |
|---|---|
| `id` | UUID 主键 |
| `content` | 记忆文本 |
| `memory_type` | `working` / `session` / `episodic` / `semantic` |
| `origin` | **`foreground`**（用户/外部写入，受 P0 保护）／ **`agent`**（系统巩固产生） |
| `scope` | 信任域：`agent` / `session` / `workspace` / `global` |
| `agent_id` / `session_id` | 归属 |
| `updated_at` | **变更检测高水位，必须 tz-aware ISO 8601 UTC** |
| `importance` | 0~1 五维评分 |
| `state` | `active` / `stale` / `archived` |

## 三种接入方式

### 方式 A — 只共享 db（最简，被动）
你的 harness 写 `memories` 表 → orchestrator **60s 轮询自动检测** → 跑确定性维护（prune/forget/migrate，**只动 `origin=agent`**）。
- 你的 `foreground` 写入靠下次对话 `recall`（每轮重查）**被动可见**，但**不被自动整理**。
- 延迟：最多 60s。
- 适合：低频写入、不急的场景。

### 方式 B — 共享 db + 写后调 `/notify`（推荐，即时）
你的 harness 写完 db → **立即 `POST /v1/memory/notify`** → orchestrator 即时跑同一维护链 + SSE 通知前端。
- 比方式 A 快（不必等 60s）。
- `force=true` 无条件跑；`force=false` 仅在有外部变更时跑。
- 适合：写完想立刻被整理/通知的场景。

### 方式 C — 调 `/consolidate`（LLM 巩固）
你的 harness 想让 orchestrator 用 **LLM 提取经验**写回（关键决策/踩坑/工具模式）：
```
POST /v1/memory/consolidate  {"agent_id":"...", "messages":[...]}
```
- 会调 LLM（有成本），8s 超时，失败降级启发式。
- 适合：任务完成后沉淀经验。

## 关键约定：`updated_at` 格式

你写入 `memories.updated_at` **必须**用 tz-aware ISO 8601 UTC，与 orchestrator 一致：

```python
from datetime import datetime, timezone
updated_at = datetime.now(timezone.utc).isoformat()
# '2026-06-20T10:00:00.123456+00:00'  ✓
# '2026-06-20T10:00:00'                ✗ 无时区 → 轮询视为远古,不检测
# '2026-06-20T12:00:00+02:00'          ✗ 非UTC字符串 → 比较错乱
```

格式不对 → 轮询**检测不到**你的写入（方式 A/B 失效）。

## 最小接入示例（Python：直写 db + 即时通知）

```python
import sqlite3, uuid
from datetime import datetime, timezone
import requests

DB = "/home/yy/projects/agent-os-v2/services/orchestrator/data/memories.db"
ORCH = "http://127.0.0.1:8000"
AGENT_ID = "<目标 agent id>"   # 见 GET /v1/agents

# 1) 直写记忆(模拟外部 harness,origin=foreground 受 P0 保护)
now = datetime.now(timezone.utc).isoformat()
conn = sqlite3.connect(DB)
conn.execute(
    "INSERT INTO memories "
    "(id,content,scope,memory_type,importance,metadata,agent_id,session_id,"
    " created_at,accessed_at,updated_at,archived,origin,state,last_state_transition) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (str(uuid.uuid4()), "外部 harness 的记忆内容", "agent", "session", 0.5, "{}",
     AGENT_ID, "", now, now, now, 0, "foreground", "active", ""),
)
conn.commit(); conn.close()

# 2) 即时通知 orchestrator 跑维护(零 LLM)
r = requests.post(f"{ORCH}/v1/memory/notify", json={"agent_id": AGENT_ID, "force": True})
print(r.json())   # {'status':'ok','processed':1,'results':[{'pruned':..., 'forgot':..., 'migrated':...}]}
```

> 直接写 db 前确认 orchestrator 已启动（否则表不存在）。也可改用 orchestrator 的 `POST /v1/memories`（会自动填 `updated_at`，但 `origin` 走服务内默认）。

## 约束清单（务必遵守）

| # | 约束 | 后果 |
|---|---|---|
| 1 | `updated_at` 必须 tz ISO UTC | 格式错 → 轮询检测不到 |
| 2 | `origin=foreground` 记忆**永不被自动归档/迁移/合并**（P0 保护） | 只能靠 `recall` 被动读，或 `/consolidate` 产生新 agent 记忆 |
| 3 | `/consolidate` **无 rate limit** | 高频调用累积 LLM 成本，调用方自行限流 |
| 4 | 单 replica 假设 | 多 replica 需额外 SQLite 锁 / 令 migrate 幂等 |
| 5 | 服务停机时轮询不跑 | 外部写入累积，重启后首个 tick 补处理（watermark 初始化为启动时刻 MAX，**启动前的写入也会被处理**） |
| 6 | 不要直接改 `origin=agent` 的记忆 | 那是系统巩固产物，会被维护链整理；你要写就用 `origin=foreground` |

## 故障排查

| 现象 | 排查 |
|---|---|
| 我的写入没被检测/维护 | ① `updated_at` 格式对吗？② 是否 > orchestrator 启动时刻？③ `/notify force=true` 强制跑一次看 results |
| `/notify` 返回 `noop` | 无外部变更（post-chain watermark 已吸收）。`force=true` 绕过 |
| `/notify` 返回 `unavailable` | `db_watcher` 未装配（orchestrator 启动异常，看日志） |
| `/consolidate` 返回 `messages_required` | 传了空 messages（防垃圾写入） |
| `/consolidate` 返回 `llm_unconfigured` | orchestrator 未配 `LLM_API_KEY`/`ANTHROPIC_AUTH_TOKEN` |
| `/consolidate` `degraded:true` | LLM 超时/失败 → 降级启发式。配 `LLM_MODEL=glm-4-flash` |
| 我写的记忆没出现在对话里 | `recall` 每轮重查，下轮对话会读到；或检查 `scope`/`archived`/`agent_id` 过滤 |

## 相关
- 完整 API 字段 / SSE 事件 / 失败模式：[`memory-external-api.md`](./memory-external-api.md)
- 记忆架构总览：[`architecture.md`](./architecture.md)
