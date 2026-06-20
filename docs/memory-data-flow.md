# Memory 数据操作 Flow（权威架构说明）

> 本文综合 write / recall / refine / kg / event / update 六维度的代码事实,描述 agent-os-v2 记忆子系统的**完整生命周期**:数据如何产生、如何存储、如何精炼(确定性 + LLM)、如何被召回、如何被 KG 索引、如何被淘汰。
>
> 代码基准:`feat/memory-provenance` 分支;主模块 `services/orchestrator/src/memory/`、`services/orchestrator/src/context/`、`services/orchestrator/src/api/routes/`。

---

## 0. 一图总览:记忆子系统的两条存储轴 + 三条驱动轴

```
                 ┌─────────────────────────────────────────────────────────┐
                 │                     触发驱动轴                          │
                 │                                                         │
                 │  (A) 事件总线 MemoryEventBus   (chat.py emit → hook)    │
                 │  (B) 时间循环 24h sweep / 60s poll / POST /notify       │
                 │  (C) 外部 API  /memories  /consolidate  (harness/前端) │
                 └────────────┬───────────────────────────────────┬────────┘
                              │                                   │
              ┌───────────────▼─────────────────┐  ┌──────────────▼──────────────┐
              │   存储轴 1: memories.db (真相源)  │  │  存储轴 2: kg.db (结构索引)   │
              │   SQLiteStore — 15 列 MemoryItem  │  │  entities + relations (图)   │
              │   origin / state / 4 级 memory_type │  │  source_memory_ids 回指     │
              └───────────────┬─────────────────┘  └──────────────┬──────────────┘
                              │                                   │
            ┌─────────────────┴──────────────────┐               │
            │                                    │               │
   ┌────────▼────────┐               ┌───────────▼────────┐      │
   │ 精炼链 (refine)  │               │  召回 (recall)     │      │
   │ prune→forget→    │               │  Keyword (内存)    │      │
   │ migrate(→KG)     │               │  KG (LIKE/BFS)     │◀─────┘
   │ reflect/compress │               │  Unified (kw‖kg)   │
   │ 均过滤 origin=AGENT│              │  Semantic 已废弃  │
   └──────────────────┘               └────────────────────┘
```

两条**存储轴**(`memories.db` vs `kg.db`)分工明确、物理隔离;三条**驱动轴**(事件总线 / 时间循环 / 外部 API)各自独立触发,但精炼链路最终汇聚到同一组确定性组件(`prune → forget → migrate`)。

---

## 1. 完整生命周期 Flow(8 步)

每步标注:**作用(why)/ 操作(what)/ 触发(when)/ 代码引用(file:line)**。

### 步骤 1 — 数据产生(Write Entry)

**作用**:把"原始信息"变成 `MemoryItem` 入库。共 9 个入口,全部汇聚到唯一的终端写。

**触发**:
- 用户/外部:`POST /v1/memories`(同步,origin=FOREGROUND)
- chat 轮次:`/chat` TURN_END(conversation_item)、`/execute` _node_llm TURN_END(working_item)、_node_tool TURN_END(tool_result_item)
- 会话结束:`/chat` SESSION_END(异步,origin=AGENT 产生 EPISODIC)
- 上下文压缩:`/execute` PRE_COMPRESS(同步/异步)
- 任务巩固:`/execute` 完成后 fire-and-forget `task_consolidator.consolidate_task`、`POST /v1/memory/consolidate`(origin=AGENT)
- 迁移:`migrate_session_to_episodic` / `migrate_episodic_to_semantic`(origin=AGENT)

**操作**(唯一终端 SQL,所有入口共享):
```sql
INSERT OR REPLACE INTO memories
  (id, content, scope, memory_type, importance, metadata, agent_id, session_id,
   created_at, accessed_at, updated_at, archived, origin, state, last_state_transition)
VALUES (:id, :content, :scope, :memory_type, :importance, :metadata, :agent_id, :session_id,
        :created_at, :accessed_at, :updated_at, :archived, :origin, :state, :last_state_transition)
```

**调用栈**:`ENTRY → MemoryService.store (service.py:118-139) → CrudOperations.store (_crud.py:31-64, 注入 uuid4 + auto-score) → SQLiteStore.store (sqlitestore.py:211-234)`。

`MemoryService.store` 默认 `origin=FOREGROUND`;事件总线/精炼组件显式传 `origin=AGENT`。

**代码引用**:`routes/memory.py:26-37`、`routes/chat.py:131-164/191-203/339-358/448-453`、`memory/service.py:118`、`memory/_crud.py:31`、`memory/sqlitestore.py:211`。

---

### 步骤 2 — 存储(Store Convergence)

**作用**:所有写都落在 `data/memories.db`(WAL,synchronous=NORMAL,foreign_keys=ON)。15 列 schema,生产装配 `MemoryService(SQLiteStore())`,**无 PostgreSQL writer**(`pg_store=None`, `_state.py:33-34`),**无 FAISS**(`vector_store=None`, D-27 移除)。

**操作**:DDL 见 `sqlitestore.py:64-118`(`CREATE TABLE IF NOT EXISTS memories` + `idx_memories_updated_at`)。增量迁移 `_migrate_schema`(`:120-149`)给旧库补 `origin`(P0)/ `state` + `last_state_transition`(P3)列,旧 `archived=1` 行回填 `state='archived'`。

`_item_to_row`(`:166-185`)把 `metadata` 序列化为 JSON、`origin`/`state` 存为 enum `.value` 字符串、`updated_at` **每次写都强制刷新为 `now()`**(无 setter)——这是 db_watcher 水位线检测的基础。

**代码引用**:`engine.py:60-61`(装配)、`_state.py:33-34`(pg_store=None)、`sqlitestore.py:64-185`。

---

### 步骤 3 — 召回(Recall)

**作用**:为 LLM 上下文/外部查询取回相关 `MemoryItem`。3 个触发点(显式 API / 每轮注入 / 子 agent 隔离),全部汇聚到 `MemoryService.recall`(service.py:174-230)。

**触发**:
- `GET /memories` / `GET /memories/layers`(query="" 走全量)
- `ContextCompiler.compile` Layer2 per-turn(`compiler.py:99-117`,`query=system_prompt[:200]`, `top_k=5`)
- `ContextManager.isolate`(子 agent 复制父上下文)

**操作**(运行时仅 2 条启用路径):
- **Keyword**:`InMemoryStore.search` 纯内存 dict 遍历过滤,**无 SQL**,按关键词子串匹配 + created_at 倒序。
- **KG**:`SELECT * FROM entities WHERE name LIKE ? [AND type=?] LIMIT ?`(`knowledge_graph.py:857-882`),取 `source_memory_ids` 回 `store.get()` 拉原文。
- **Unified**(配置 KG 时自动升级):Keyword ‖ KG 并发,加权 kw=0.4 / kg=0.6 合并排序。
- **Semantic/向量**:**已废弃**(`service.py:78` `vector_store=None`,`_recall/__init__.py` 不导出 `SemanticRecall`,死代码)。

**代码引用**:`service.py:174-230`、`_recall/keyword_recall.py:31-44`、`_recall/kg_recall.py:33-59`、`_recall/unified_recall.py:40-128`、`store.py:119-154`。

---

### 步骤 4 — 精炼:确定性三段链(prune → forget → migrate)

**作用**:零 LLM 的"沉淀"链路,把低层级记忆逐级升华为高层级,并归档陈旧项。**全程过滤 `origin=AGENT`**,FOREGROUND 永不参与。

**触发**(两个共享入口,per-agent `asyncio.Lock` 防竞态):
- **60s 被动轮询**:`engine._watch_loop`(`engine.py:163-184`)`sleep(60)` → `db_watcher.has_external_changes`(MAX(updated_at) 水位线,`:76-90`)→ `run_once_all(trigger='poll')`。
- **24h 主动清扫**:`engine._start_forgetting_sweep`(`engine.py:124-160`)`sleep(86400)` → 每 agent 同样三段。
- **按需**:`POST /v1/memory/notify`(`memory.py:96-126`)→ `run_maintenance(trigger='notify')`,与 60s 共享锁。

**操作**(顺序固定,各自 try/except 独立守卫):

| # | 组件 | 操作 | 关键阈值 |
|---|------|------|----------|
| (1) | `TimeBasedStatePruner.prune` | `UPDATE memories SET state/last_state_transition/archived` | age≥90d 或 importance<0.1→ARCHIVED;age≥30d→STALE |
| (2) | `ActiveForgetting.run_sweep` | `UPDATE memories SET archived=1, state='archived'` | STALE 直接归档;ACTIVE 需 score<0.1 AND age≥24h AND 无 relations |
| (3) | `EpisodicToSemanticMigrator.migrate` | `INSERT` 新 SEMANTIC 实体/关系行 | 正则抽取;recall 软去重;不删源 |

水位线 post-chain 重锚(`db_watcher.py:187`)防止自激循环。

**代码引用**:`db_watcher.py:105-192`、`state_pruner.py:80-131`、`forgetting.py:82-163`、`migrator.py:357-384`。

---

### 步骤 5 — 精炼:LLM/启发式巩固(task_consolidator + reflect + compress)

**作用**:用 LLM 或启发式从原始记忆中抽取"决策/pitfall/摘要",产出高层级记忆。

**触发与操作**:

| 组件 | 触发 | 操作 | LLM? |
|------|------|------|------|
| `TaskConsolidationAgent.consolidate_task` | `/execute` 完成后 fire-and-forget / `POST /v1/memory/consolidate` | `BackwardWriter` 按 confidence 三通道 INSERT:FAST(≥0.8→WORKING/0.9)、MEDIUM(0.5-0.8→SEMANTIC/0.6)、SLOW(<0.5→EPISODIC/0.4);LLM 失败→`_degrade`(EPISODIC/0.4, metadata.degraded=True) | **是**(timeout 8s,无 api_key 则 no-op) |
| `SessionToEpisodicMigrator.migrate` | SESSION_END 事件(`on_session_end`) | 按小时窗口分组 SESSION → INSERT EPISODIC(importance=max 组内) | 否(纯启发式) |
| `WorkingToSessionMigrator.flush` | 节点执行后 TURN_END | IMPORTANCE 评分 → INSERT SESSION(metadata.migrated_from=working) | 否 |
| `SessionOperations.reflect` | periodic(`dreamer.py` sideline) | Jaccard overlap≥0.4 分组,len≥2 合并 → INSERT SEMANTIC(content=`\n---\n` join) | 否(纯启发式) |
| `CompressionEngine.compress_items` | PRE_COMPRESS(token 70% 异步/85% 同步) | target_ratio=0.3 截断,group_size=3 分组摘要 → 返回 (retained, summaries) 由上层 store;非保留项 `UPDATE archived=1` | 是(可选 summarise_fn) |

**全链 NO-DELETE**:合并/迁移/reflect/压缩都是 **INSERT 新高层级行 + 源行保留**(`metadata.source_ids` 反向引用维持可溯)。

**代码引用**:`sideline/task_consolidator.py:61-152`、`sideline/backward_writer.py:118-315`、`migrator.py:101-201`、`_session.py:43-136`、`compressor.py:153-359`、`default_hook.py:108-232`。

---

### 步骤 6 — KG 索引(extract_and_ingest)

**作用**:每轮对话异步抽取实体/关系,写入独立的 `data/kg.db`,作为召回的索引跳板。**原文不入 KG**。

**触发**:chat.py `_trigger_kg_extraction`(`chat.py:62-79`)每完成一轮(`_node_llm` 145 / 流式 254 / 工具 360)调一次,`asyncio.create_task(asyncio.to_thread(kg.extract_and_ingest, ...))` fire-and-forget,全 try/except 兜底。

**操作**:
- 实体抽取:7 条 regex(CAPITALIZED_PHRASE / QUOTED_STRING / TECHNICAL_TERM / CAMEL_CASE / PASCAL_TECH / ALLCAPS_PASCAL / SNAKE_CASE),name 去重后截断 [:20]。`add_entity` 大小写不敏感查重,命中合并 properties/source_memory_ids,否则 `INSERT OR IGNORE`。
- 关系抽取:7 条谓词 regex(is_a / uses / depends_on / contains / belongs_to / implements / connected_to),截断 [:15]。`add_relation` 先 `_resolve_entity_id`(找不到建 auto_detected 占位实体),再 INSERT relations 行,confidence 默认 1.0。
- 软删除:`expire_relation` 设 `valid_to=now()`,查询一律过滤 `valid_to IS NULL`。
- 图遍历:`query_neighbors` / `expand` / `shortest_path` 用递归 CTE BFS(`WITH RECURSIVE traverse ...`),max_depth≤10。

**regex 局限**:纯规则无语义,中文/小写普通名词基本漏掉;关系谓词固定 7 种强依赖英文句式 `X uses Y`;无实体类型推断(type 靠 pattern 静态打标);无置信度回填(全 1.0)。

**代码引用**:`knowledge_graph.py:96-257`(regex)、`:438-568`(add_entity/add_relation)、`:798-846`(BFS)、`:907-938`(extract_and_ingest)、`chat.py:62-79`、`engine.py:56-62`(装配)。

---

### 步骤 7 — 淘汰(Prune + Forget = 软归档)

**作用**:把陈旧/低价值记忆从 ACTIVE 推到 STALE/ARCHIVED,控制膨胀。**物理行不删**(软归档)。

**触发**:同步骤 4 的确定性链第 (1)(2) 步。

**状态机**(`MemoryState` 流转 ACTIVE→STALE→ARCHIVED):
```
ACTIVE ──age≥30d──► STALE ──(forget 快速路径)──► ARCHIVED
   │                                                  ▲
   └──age≥90d 或 importance<0.1──────────────────────┘
```

**操作**:`UPDATE memories SET state=?, last_state_transition=?, archived=?, updated_at=now()`。
- `archived` 布尔旗标与 `state` 双写(向后兼容),由 `MemoryItem.__post_init__`(`types.py:147-159`)在构造时同步。
- `recover()` 仅 `archived=False`(`sqlitestore.py:183`)。

**代码引用**:`state_pruner.py:70-131`、`forgetting.py:64-170`、`types.py:52-86/107-167`。

---

### 步骤 8 — 跨 agent 隔离(Permissions)

**作用**:防止跨 agent 读写越权。

**触发**:`CrudOperations` 在 `get`/`update`/`delete` 三处做网关。

**操作**:5 级权限 L0 NONE / L1 METADATA / L2 SUMMARY / L3 FULL / L4 ADMIN。同 agent 直接放行;跨 agent 默认 L2 SUMMARY(只读脱敏摘要),写/删要求 ≥ADMIN。`filter_content` 按级别脱敏:FULL 返原文 / SUMMARY 截断 120 字 / METADATA 返 `[N chars, access restricted]` / NONE 返 `[access denied]`。

**代码引用**:`permissions.py:33-41/215-285`、`_crud.py:90-139`。

---

## 2. 两个场景对比:常规 session vs 外部 harness API

| 维度 | 常规 session(chat.py `/execute` + `/chat` + 24h sweep) | 外部 harness API(`/memories` `/notify` `/consolidate`) |
|------|--------------------------------------------------------|--------------------------------------------------------|
| **触发来源** | 用户在 chat 里说话 / 后台循环 | 外部进程/harness/前端主动调 HTTP |
| **origin 标记** | 事件总线产生的多带 `origin=AGENT`(SESSION_END、migrate、consolidate);`/chat` conversation_item 默认 FOREGROUND | `POST /memories` 默认 `origin=FOREGROUND`(P0 受保护) |
| **步骤 1 写入** | `/execute` _node_llm/_node_tool TURN_END、`/chat` TURN_END、SESSION_END | 仅 `POST /memories`(直接 store) |
| **步骤 3 召回** | 每轮 `compiler.compile` Layer2 自动注入 top_k=5 | `GET /memories` / `GET /memories/layers`(显式查) |
| **步骤 4 确定性精炼** | 24h sweep 自动跑 prune→forget→migrate | **`POST /notify` 主动触发同一链**(force=True 绕水位线) |
| **步骤 5 LLM 巩固** | `/execute` 完成后 fire-and-forget `consolidate_task` | `POST /consolidate` 同步触发(timeout 8s,失败降级) |
| **步骤 6 KG 索引** | 每轮 `_trigger_kg_extraction` 异步抽 | 不触发(无对话轮次) |
| **P0 保护** | AGENT 记忆被精炼;FOREGROUND conversation_item 不动 | `POST /memories` 写入即 FOREGROUND,**永不被自动精炼/归档/迁移** |
| **SSE 推送** | `/execute` event_stream 中继 memory_event(prune/forget/migrate/compress) | `/notify` 触发链路时同样 emit SSE |

---

## 3. memories.db vs kg.db 分工

| 维度 | memories.db(SQLiteStore) | kg.db(KnowledgeGraph) |
|------|--------------------------|------------------------|
| **角色** | **真相源(source of truth)** | **结构索引(index)** |
| **存什么** | 完整 `MemoryItem`:原文 content + 元数据 + origin provenance + state + memory_type(WORKING/SESSION/EPISODIC/SEMANTIC) | 仅实体-关系结构:entities(name/type/properties/source_memory_ids) + relations(source_id/target_id/relation_type/valid_from/valid_to) |
| **怎么产生** | 9 个写入入口(步骤 1)全部落这里 | `extract_and_ingest` regex 抽取(步骤 6),原文**从不进 KG** |
| **怎么查** | Keyword 内存 dict 遍历 / KG 回填 `store.get(mid)` | LIKE 子串搜实体 + 递归 CTE BFS 遍历关系 |
| **关联方式** | KG 的 `source_memory_ids` / `source_memory_id` 字符串 id **回指** memories.db(非 SQL 外键,软引用) | — |
| **故障隔离** | KG 坏/空不影响 memories.db,反之亦然 | — |
| **表结构** | `memories`(15 列)+ `memory_blocks` + `sessions` + `memory_versions` | `entities` + `relations` |
| **写并发保护** | WAL 单写 | `kg_write_lock.py` per-branch 写锁 |

**recall 协作**:KG recall 命中实体后,用 `entity.source_memory_ids` 回 `memories.db` 取原文——KG 只当"跳板索引"。

---

## 4. LLM 依赖标注

| 环节 | LLM? | 说明 |
|------|------|------|
| 步骤 4 prune | **零 LLM** | 纯时间/importance 列判(`state_pruner`) |
| 步骤 4 forget | **零 LLM** | STALE 快速路径确定;ACTIVE 走五维**确定性打分器**(非 LLM) |
| 步骤 4 migrate(正则实体/关系抽取) | **零 LLM** | `_extract_entities`/`_extract_relations` 纯 regex |
| 步骤 5 reflect | **零 LLM** | Jaccard 启发式合并 |
| 步骤 5 WorkingToSession/SessionToEpisodic | **零 LLM** | 评分 + 小时窗口分组 |
| 步骤 5 task_consolidator | **是** | timeout 8s,失败降级 `_degrade` 启发式;无 api_key 则 no-op |
| 步骤 5 compress | **可选 LLM** | `summarise_fn`,有默认启发式 fallback |
| 步骤 6 KG extract | **零 LLM** | 7+7 条 regex(局限见步骤 6) |
| 召回(所有路径) | **零 LLM** | 纯检索 + 加权合并 |

---

## 5. P0 origin 保护边界(foreground vs agent)

| origin | 含义 | 来源 | 被自动精炼? |
|--------|------|------|-------------|
| `FOREGROUND` | 用户/外部直接录入 | `POST /memories`、`/chat` conversation_item(`MemoryService.store` 默认值) | **永不**。只能由用户显式 update/delete 或 `destroy_session` 改动 |
| `AGENT` | 系统自动沉淀 | SESSION_END migrate、task_consolidator、WorkingToSession、EpisodicToSemantic、reflect | **是**。全精炼链可触碰 |

**保护点**:所有自动维护组件用 `MemoryFilter(origin=MemoryOrigin.AGENT)` 做硬前置过滤:
- `state_pruner.prune`(`state_pruner.py:80-84`)
- `forgetting.run_sweep`(`forgetting.py:82-86`)
- `migrate_session_to_episodic`(`migrator.py:363-367`)
- `migrate_episodic_to_semantic`(`migrator.py:378-382`)
- `SessionOperations.reflect`(`_session.py:71`)

**例外**:compress 不按 origin 过滤(按 token 触发);`destroy_session` 批量删 session 全部 memory(无 origin 过滤,session 销毁语义)。

---

## 6. SQL 操作汇总(谁、改什么列、何时)

### INSERT(唯一终端:`SQLiteStore.store`,全部 `INSERT OR REPLACE`)

| 调用方 | memory_type | origin | 触发 |
|--------|-------------|--------|------|
| `MemoryService.store`(默认 facade) | 任意 | FOREGROUND | `POST /memories` / TURN_END conversation_item |
| `migrator.WorkingToSession.flush` | SESSION | AGENT | 节点执行后 TURN_END |
| `migrator.SessionToEpisodic.migrate` | EPISODIC | AGENT | SESSION_END / 24h sweep(间接) |
| `migrator.EpisodicToSemantic.migrate` | SEMANTIC | AGENT | 60s/24h/notify 链第 (3) 步 |
| `BackwardWriter._write_fast/medium/slow` | WORKING/SEMANTIC/EPISODIC | AGENT | task_consolidator |
| `task_consolidator._degrade` | EPISODIC | AGENT | LLM 失败降级 |
| `_session.reflect` | SEMANTIC | AGENT | periodic |
| `compressor` summary | 继承 | 继承 | PRE_COMPRESS |

### UPDATE(`SQLiteStore.update`,全量 13 列覆写;`updated_at` 每次强制 = now)

| 调用方 | 改的关键列 | 触发 |
|--------|-----------|------|
| `CrudOperations.update` | content/scope/importance/metadata 等业务列 | 显式 API update |
| `SQLiteStore.get`(读副作用) | `accessed_at = now`(单列 UPDATE) | 每次召回读取——读即续命 |
| `TimeBasedStatePruner.prune` | state / last_state_transition / archived | 60s/24h/notify 链第 (1) 步 |
| `ActiveForgetting._archive` | archived=1 / state='archived' / metadata.forget_score | 第 (2) 步;STALE 确定性归档 |
| `ActiveForgetting` safety 计数 | metadata.safety_turns_remaining 递减 | 非归档 |
| `archive_session` | archived=1(批量,旧布尔旗标路径) | session 归档 |
| `recover` | archived=False | 恢复 |

### DELETE(**几乎不用**——日常归档全走 UPDATE,物理行长期保留)

| 调用方 | SQL | 何时用 |
|--------|-----|--------|
| `SQLiteStore.delete`(单条) | `DELETE FROM memories WHERE id=?` | 仅 ADMIN 显式删(`_crud.py:118-139` 权限网关) |
| `SQLiteStore.destroy_session`(批量) | `DELETE FROM memories WHERE session_id=?` | session 销毁——**少数能物理删 FOREGROUND 的路径**,无 origin 过滤 |

**关键设计**:精炼/归档/迁移/reflect/压缩**全部 NO-DELETE**,源行保留 + `metadata.source_ids` 反向引用维系可溯。recover 可恢复软归档。

---

## 7. 事件驱动:两条独立通道

| 通道 | 性质 | 作用 | 触发 |
|------|------|------|------|
| **A. MemoryEventBus**(内部 lifecycle bus) | 结构化生命周期 hook 总线 | 编排/执行——chat→hook→真实 memory_service/migrator 调用,有返回值传递(如 PRE_COMPRESS 的 summary_ids) | 6 个 EventType:`SESSION_START` / `TURN_START`(P2 保留) / `TURN_END`(3 调用点) / `PRE_COMPRESS` / `SESSION_END` / `DELEGATE`(P3/P4 保留)。chat.py 的 6 个 emit 点触发 |
| **B. SSE 广播**(`emit_memory_event`) | 推送给前端订阅者 | 观测/推送——只广播给 `/execute` event_stream 的 queue 订阅者,**无返回值,不触发任何记忆操作** | DefaultMemoryHook 在完成副作用后调 `_emit_sse` → put_nowait 到各 subscriber queue(maxsize=200)。事件名:prune/forget/migrate/compress |

`DefaultMemoryHook` 同时跨越两层:在 A 里执行操作,在 B 里发通知。降级开关 `MEMORY_EVENT_BUS_ENABLED=0` 时 `hooks()` 仅留 SYSTEM、跳过 OBSERVER(等价 pre-P1)。

**端到端 execute 顺序**:
```
POST /execute
  → emit SESSION_START (create_session)
  → 图 _node_llm: emit TURN_END(working_item → migrate L0→L1)
                  + emit PRE_COMPRESS(SYNC 落库+返回 summary_ids / ASYNC 后台回调+SSE compress)
  → [若 needs_tool] _node_tool: emit TURN_END(tool_result_item → store)
  → _node_llm_synthesize
  → 图完成: fire-and-forget task_consolidator.consolidate_task(LLM 巩固,非 bus)
            + SSE execution_complete
后台并行: 24h sweep + 60s db_watch(与 /notify 共享确定性链,各自独立触发)
```

---

## 附:关键代码索引

| 模块 | 文件 |
|------|------|
| 写入收敛 | `memory/service.py:118-139`、`_crud.py:31-64`、`sqlitestore.py:211-234` |
| 召回路由 | `memory/service.py:174-230`、`_recall/{keyword,kg,unified,shared}_recall.py` |
| 确定性精炼 | `state_pruner.py:70-131`、`forgetting.py:64-170`、`migrator.py:357-384`、`db_watcher.py:105-192` |
| LLM 巩固 | `sideline/task_consolidator.py:61-152`、`sideline/backward_writer.py:118-315`、`compressor.py:153-359` |
| KG | `knowledge_graph.py:96-938`、`kg_query_interface.py:24-50`、`chat.py:62-79` |
| 事件总线 | `event_bus.py:35-133`、`hooks.py:28-141`、`default_hook.py:73-232` |
| 状态机/P0 | `types.py:52-86/107-167`、`state_pruner.py:80`、`forgetting.py:82`、`migrator.py:363/378` |
| 权限 | `permissions.py:33-41/215-285`、`_crud.py:90-139` |
| 生产装配 | `engine.py:56-61`(KnowledgeGraph + MemoryService(SQLiteStore()))、`_state.py:33-34`(pg_store=None)、`engine.py:124-184`(24h sweep + 60s poll) |
| 路由 | `routes/memory.py:26-163`、`routes/chat.py:131-467` |
