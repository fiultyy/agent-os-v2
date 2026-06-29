# 记忆外部维护 API 与轮询机制

> 面向**与其他 harness 共享 `data/memories.db`** 的场景。orchestrator 提供三层"记忆维护"接入面：一个自动轮询 + 两个外部接口。

## 概述

| 接入面 | 机制 | LLM | 触发方式 | 触达 |
|---|---|---|---|---|
| **轮询 tier(1)** | `MemoryDBWatcher` 60s loop 的确定性链 | ❌ 零 LLM | 自动（检测外部 db 写入） | `prune → forget → migrate`（仅 `origin=AGENT`） |
| **轮询 tier(2)/(3)** | 同一 60s loop 的 idle 链 | ✅ LLM（`MEMORY_INGESTOR_ENABLED` / `MEMORY_CONSOLIDATOR_ENABLED` 开启时） | 每个 60s tick 无条件入口，内部按 write-idle + per-agent cadence 闸自门控 | idle ≥ `MEMORY_IDLE_THRESHOLD` → `IngestorAgent.ingest`（批量上限 `MEMORY_IDLE_EXTRACT_BATCH_LIMIT`=50）；idle ≥ `MEMORY_IDLE_CONSOLIDATE_THRESHOLD` → `emit CONSOLIDATE`（`ConsolidatorAgent`），trigger=`idle_poll` |
| **`/memory/notify`** | 外部应用主动请求 | ❌ 零 LLM | 外部应用写库后调用 | 同 tier(1)（即时） |
| **`/memory/consolidate`** | 外部应用主动请求 | ✅ LLM | 外部应用按需触发 | `task_consolidator` 经验提取 |

**Provenance 保证（P0）**：所有确定性维护（轮询 + `/notify`）**只动 `origin=AGENT` 的记忆**；`origin=FOREGROUND`（用户 / 外部应用写入）的记忆受保护，**永不被自动归档 / 迁移 / 合并**。外部写入的 foreground 记忆靠下次对话 `recall`（compiler 每轮重查）被动可见，但不被自动整理——这是设计上的安全边界，不是缺陷。

**零侵入（签名层）**：本机制不改任何现有 `store/recall/prune/forget/migrate` 签名，不碰红线（`butterfly_wing` / `permissions` / `scorer` / `_recall`）。`/consolidate` 复用现有 `task_consolidator`。**注**：60s loop 的 tier(2)/(3) idle 链（`run_idle_maintenance` / `_run_extract`）是新写入 watcher 模块的 LLM 驱动代码，复用既有 `IngestorAgent` / `ConsolidatorAgent`（经 `_state.ingestor` / `_state.consolidator`），受 `MEMORY_INGESTOR_ENABLED` / `MEMORY_CONSOLIDATOR_ENABLED` env gate（默认关）——即「不新增 LLM 客户端/模型，但 loop 结构确含 LLM tier」。

---

## 1. 轮询维护（MemoryDBWatcher）

### 1.1 外部写入如何被检测 — `updated_at` watermark

`MemoryDBWatcher` 通过 `memories.updated_at` 的高水位（watermark）检测外部进程的写入：

- 每个 60s tick 读 `SELECT MAX(updated_at) FROM memories`（走 `idx_memories_updated_at` 索引，index-only scan，经 `asyncio.to_thread` 卸载到线程，不阻塞 event loop）。
- 与 `_last_watermark` 比较：**用解析后的 `datetime` 比 `>`**（非字符串 `!=`，对外部时间戳格式漂移鲁棒）。
- `__init__` 时 watermark 初始化为当前 `MAX(updated_at)`，**因此首次 poll 只处理进程启动后的新写入**（不重处理历史数据）。
- **post-chain 重锚**：维护链本身（prune/forget/migrate）会 `update()`/`store()` 改写 `updated_at`。chain 成功后**重新读 `MAX(updated_at)` 作为新 watermark**，吸收维护自身的写，**避免每 60s 无限循环维护**。

**时间戳格式约定**：外部应用写入 `updated_at` 必须用 tz-aware ISO 8601 UTC，与 `datetime.now(timezone.utc).isoformat()` 一致（例 `2026-06-20T10:00:00.123456+00:00`）。无法解析的时间戳视为远古，不触发维护（不因垃圾数据空跑）。

### 1.2 60s loop — tier(1) 确定性链（零 LLM）+ tier(2)/(3) idle 链（LLM）

60s loop（`_watch_loop`）是**三层**结构：

- **tier(1) 确定性链（零 LLM）**：仅当检测到外部写入（`has_external_changes` 返回非 None）时，对每个 agent 跑确定性链（顺序、参数与 24h `_sweep_loop` 一致）：

```
state_pruner.prune(agent_id)           # ACTIVE→STALE→ARCHIVED(30/90 天)
  → active_forgetting.run_sweep(agent_id)  # 五维评分低 → 归档
  → migrator.migrate_episodic_to_semantic(agent_id)  # episodic→semantic
```

- **每步独立 `try/except`，永不抛**；任一步失败只记 `last_error`，不阻断其余。
- **per-agent `asyncio.Lock`**：`/notify` 与 60s loop 对同一 agent 不会重叠（防止 migrate 竞态产生重复 semantic 记忆）。
- 周期可配：`MEMORY_DB_WATCH_INTERVAL`（秒，默认 60）控制 60s poll tick。此外 watcher 的 LLM idle tier（extract/consolidate）由四个写空闲阈值 env 控制：`MEMORY_IDLE_THRESHOLD`（默认 60，extract/consolidate 触发所需的写空闲下限）、`MEMORY_IDLE_EXTRACT_INTERVAL`（默认 300，per-agent extract 最小间隔）、`MEMORY_IDLE_CONSOLIDATE_THRESHOLD`（默认 300，per-agent consolidate 最小间隔）、`MEMORY_IDLE_EXTRACT_BATCH_LIMIT`（默认 50，单次 extract sweep 批量上限，防长 LLM 批阻塞确定性链）。
- 与现有 24h `_sweep_loop` **并存互不干扰**（两个独立 startup hook）。

- **tier(2) extract（IngestorAgent，glm-4-flash / anthropic 侧通道，需 `MEMORY_INGESTOR_ENABLED=1`）**：每个 60s tick **无条件调用 `run_idle_once_all(trigger="idle_poll")`**，内部按 idle ≥ `MEMORY_IDLE_THRESHOLD` AND per-agent cadence `MEMORY_IDLE_EXTRACT_INTERVAL` 期满 AND `pending_extract>0` 自门控；满足时批量（上限 `MEMORY_IDLE_EXTRACT_BATCH_LIMIT`=50）对 `origin=AGENT` 且 `metadata.extracted` 未置的记忆调 `_state.ingestor.ingest(...)`，处理后标 `metadata.extracted=True` 去重。
- **tier(3) consolidate（ConsolidatorAgent，需 `MEMORY_CONSOLIDATOR_ENABLED=1`）**：idle ≥ `MEMORY_IDLE_CONSOLIDATE_THRESHOLD` AND per-agent cadence 期满时，`emit EventType.CONSOLIDATE` → `ConsolidatorHook` → `ConsolidatorAgent`（仅动 `origin=AGENT` episodic → semantic）。
- **idle debounce**：检测到新写入会 reset write-idle clock，把 tier(2)/(3) 推迟到下一个静默窗；只有零成本的 tier(1) 在写入持续期间继续跑。
- env gate 默认关闭（`MEMORY_INGESTOR_ENABLED` / `MEMORY_CONSOLIDATOR_ENABLED` 默认 `"0"`），未开启时 `_state.ingestor` / `_state.consolidator` 为 None，内部 `is not None` 守卫使 tier(2)/(3) 成 no-op。
- P0 红线：tier(2) query 层 `filter origin=AGENT` + 每项复查 FOREGROUND；tier(3) 仅动 AGENT episodic；FOREGROUND 记忆三层均不触达。

### 1.3 SSE 事件

维护沿用**现有事件名** `prune` / `forget` / `migrate`（不造新名，不破坏现有订阅者），仅追加 `trigger` 字段区分来源：

```json
event: memory_event
data: {"event": "prune", "agent_id": "<id>", "trigger": "poll|notify", "scanned": N, "stale": N, "archived": N}
```
（`forget` / `migrate` 同形，字段分别为 `{scanned, archived}` / `{path, count}`）

> 注意：外部写入本身**不产生** SSE 事件（外部写绕过事件总线）；订阅者只在维护运行后看到**汇总计数**，看不到逐条外部写。

---

## 2. `POST /v1/memory/notify` — 按需确定性维护（零 LLM）

外部应用写完 `memories` 表后**立即**调用，触发与轮询相同的确定性链（不必等 60s）。

### 2.1 请求

```json
{ "agent_id": "", "force": false, "curate": false }
```
| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `agent_id` | string | `""` | 空 = 对所有 agent 跑；指定 = 只对该 agent |
| `force` | bool | `false` | `true` = 无条件跑（绕过 `updated_at` 变更检查） |
| `curate` | bool | `false` | `true` 且已装配 CuratorAgent 时，**fire-and-forget**（独立 async task）触发一次 ④ CuratorAgent 经验整理，与同步确定性链隔离；不影响零 LLM 的 prune/forget/migrate |

### 2.2 响应

```json
{
  "status": "ok",
  "processed": 1,
  "results": [
    {"agent_id": "<id>", "trigger": "notify", "pruned": {...}, "forgot": {...}, "migrated": N, "status": "ok"}
  ],
  "curate_triggered": false
}
```
- `curate_triggered: bool` — `true` 表示本次已异步派发 CURATE 事件（需 `curate=true` 且 CuratorAgent 已装配）；`false` 表示未派发（CuratorAgent 未装配或未请求）。该 fire-and-forget 整理任务**不进入**同步 `run_maintenance` 零 LLM 链。
- `status: "unavailable"`（`db_watcher` 未装配）→ `{"status":"unavailable","results":[]}`
- `status: "noop"`（`force=false` 且无外部变更）→ 该 agent 跳过
- 每个结果含 `pruned`/`forgot`/`migrated` 计数；`status: "skipped"` 表示该 agent 正在维护中（锁占用）。

### 2.3 curl

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/notify \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "", "force": true}'
```
```bash
# 触发一次 fire-and-forget CuratorAgent 整理（需 CuratorAgent 已装配）
curl -X POST http://127.0.0.1:8000/v1/memory/notify \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "<id>", "curate": true}'
```

---

## 3. `POST /v1/memory/consolidate` — 按需 LLM 巩固（双模式）

本端点支持两种 `mode`：
- **默认 `mode=task_consolidator`**：从 `messages` 提取关键决策 / 踩坑 / 工具模式，经 `BackwardWriter` 按置信度写回（复用现有 `task_consolidator`，不新增 LLM 代码）。
- **`mode=merge`**：触发 ② `ConsolidatorAgent`，对既有 episodic 记忆做 episodic→semantic 的理解驱动合并（LLM，与上述任务后经验沉淀是两条独立路径）。

### 3.1 请求

```json
{ "agent_id": "<id>", "session_id": "<id>", "messages": [{"role":"user","content":"..."}], "timeout": 8.0, "mode": "task_consolidator" }
```
| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `agent_id` | string | `""` | 写回归属 agent（`merge` 模式缺省时取首个 agent） |
| `session_id` | string | `""` | 写回归属 session（仅 `task_consolidator` 用） |
| `messages` | list[dict] \| null | null | 待提取的对话；**空则拒绝**（仅 `task_consolidator`） |
| `timeout` | float | 8.0 | **被 clamp 到 ≤ 8.0**（防 worker 耗尽） |
| `mode` | string | `"task_consolidator"` | `task_consolidator`=任务后经验提取；`merge`=episodic→semantic 合并（`_state.consolidator` 未装配时返回 `unavailable`/`consolidator_disabled`） |

### 3.2 响应

**`mode=task_consolidator`（默认，映射 `ConsolidateResult`）：**

```json
{
  "triggered": true,
  "degraded": false,
  "confidence": 0.9,
  "written": true,
  "memory_id": "<id>",
  "channel": "fast",
  "error": null
}
```

**`mode=merge`（映射 `ConsolidatorAgent` 合并结果）：**

```json
{
  "triggered": true,
  "degraded": false,
  "written": false,
  "merged_count": 3,
  "semantic_ids": ["<id>", "<id>"],
  "archived_ids": ["<id>"],
  "error": null
}
```
（无 `confidence` / `memory_id` / `channel`；`_state.consolidator` 未装配时返回 `{"status":"unavailable","reason":"consolidator_disabled"}`。）

### 3.3 行为与防护

- **timeout clamp**：`min(req.timeout, 8.0)`——外部应用不能通过大 timeout 长占 worker。
- **空 `messages` 拒绝**：返回 `{"triggered":false,"written":false,"error":"messages_required"}`，避免每次调用写一条启发式垃圾 EPISODIC。
- **LLM 未配检测**：`LLM_API_KEY`/`ANTHROPIC_AUTH_TOKEN` 均空时返回 `{"error":"llm_unconfigured"}` 且**不写**（避免无 LLM 部署里用启发式摘要污染记忆）。
- **降级**：LLM 调用失败/超时 → `task_consolidator` 自动降级为启发式 EPISODIC 摘要（`degraded:true`, confidence 0.4, origin=AGENT）。
- ⚠️ **无限流**：本端点**未做 rate limit**。外部高频调用会累积 LLM 成本——建议调用方自行限流（后续可加 token bucket，见技术债）。

### 3.4 curl

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/consolidate \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"<id>","session_id":"<sid>","messages":[{"role":"user","content":"用 FastAPI 部署 PostgreSQL 遇到连接池泄漏,用 SQLAlchemy pool_pre_ping 解决"}]}'
```

---

## 4. Provenance 与向后兼容

- **P0 保护**：`prune` / `forget` / `migrate` 三阶段内部均 `filter origin=AGENT`；外部应用写入的 `origin=FOREGROUND` 记忆**永不被自动触碰**（只能通过 `/consolidate` 显式产生新的 AGENT 记忆，或靠 `recall` 被动读取）。
- **ADD-only**：未改动任何现有端点签名、`store/recall` 签名、24h sweep loop。
- **多 replica**：当前假设单 replica（per-agent `asyncio.Lock` 只防进程内并发）。多 replica 部署下，各 replica 的 watcher 独立轮询，可能重复维护——需 SQLite advisory lock 或令 `migrate_episodic_to_semantic` 按源 id 幂等（见技术债）。

---

## 5. 失败模式与降级

| 场景 | 行为 |
|---|---|
| `db_watcher` 未装配 | `/notify` 返回 `unavailable`；loop 不启动 |
| 外部 `updated_at` 格式异常 | 视为远古，不触发维护（不空跑） |
| 维护某步抛错 | 该步记 `last_error`，其余步继续，整体不抛 |
| `/notify` 与 loop 同时触发同 agent | 后者返回 `skipped: in_progress`（per-agent lock） |
| LLM 未配 `/consolidate` | 返回 `llm_unconfigured`，不写 |
| LLM 调用超时/失败 `/consolidate` | 自动降级启发式 EPISODIC（`degraded:true`） |
| 服务停机 | 轮询不跑；外部写入累积，**下次启动后首个 tick 检测到并处理**（watermark 初始化为启动时刻 MAX，故启动前写入会被处理——见 1.1） |
| SSE 订阅者队列满 | 该订阅者被静默移除（既有策略，防内存泄漏） |

### 已知限制（技术债）
- `/consolidate` 无 rate limit / 并发上限。
- `migrate_episodic_to_semantic` 非完全幂等（多 replica 竞态可能重复 semantic）。
- `MemoryDBWatcher` 的 watcher 健康状态尚未接入 `/health`（task handle 已存于 `_state._db_watch_task`，可后续暴露）。
