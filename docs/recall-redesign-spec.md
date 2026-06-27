# 召回架构重设计 Spec(openclaw ↔ agent-os-v2)

> 缘起:openclaw memory-recall 当前用 `normalizeKeyword`(prompt 首句截取)做召回 keyword,语义无效;且 agent-os-v2 召回只对外吐平铺列表,浪费内部已算的 LIF/KG/蝴蝶翼图结构。
> **目标**:side agent 实体提炼 → 实体召回 → agent-os-v2 返回**图结构**(LIF+KG+蝴蝶翼)→ openclaw **下一轮**作「记忆提醒」注入。
> **红线**:蝴蝶翼双向联想 / 信任域 / 五维评分(score 内存计算)/ origin provenance 不动;图返回**只读组装中间结果**,不改变召回打分逻辑。

---

## 0. 审查结论(纠正审计认知)

审计 Gap4"蝴蝶翼不在召回链"**过时/错误**。真实状态(2026-06-21 代码核查):

| 组件 | 现状 | 证据 |
|---|---|---|
| RetrieverAgent | `match_score × lif_weight` 双因子 + `_kg_entity_hit_bonus` | `sideline/retriever_agent.py:65,127,170,213` |
| 蝴蝶翼 | `ButterflyRecallStrategy` 是召回策略,wing filtering + match×lif ranking | `butterfly_wing.py:687,712,727` |
| 神经场 LIF | `_spread` 沿 KG 边扩散 + `lif_weight` + `_kg_neighbors` | `neural_field.py:259,335,101` |

**即 LIF+KG+蝴蝶翼三因子内部已协同打分召回**。Gap 不是"没接入",而是 **GET /memories 只吐平铺排序列表(memory.py:143 `_mem_to_dict(item, score)`),丢弃了图结构**。本 spec 的环节③ = 把内部已算的图组装暴露。

---

## 1. 四阶段

### 阶段 0 — openclaw 底座(执行 session 进行中,暂停渲染)
Gap3/4 的 openclaw 消费侧基础(scope 传递 + origin/state 解析)。是图召回的前提底座,但 **暂停 formatRecallBlock 平铺渲染**(阶段 3 要图渲染,避免返工)。

### 阶段 1 — agent-os-v2 图召回端点 【承重】
新增 `GET /v1/memory/graph`,组装内部图结构返回。

### 阶段 2 — openclaw side agent 实体提炼
用 `runtimeContext.llm.complete` 调简单模型,从最新 turn prompt 提炼实体关键字,替代 `normalizeKeyword` 首句截取。

### 阶段 3 — openclaw 异步记忆提醒流水线
turn N 提炼实体 + 调图召回(不阻塞当轮)→ 跨 turn 缓存 → turn N+1 assemble 把图作「记忆提醒」注入(图渲染)。复用 `spawnRecallCache` 跨 turn 模式。

### 阶段 4 — 联调 + 验收

---

## 2. 环节③图结构 schema(阶段 1 核心)

**新端点**:`GET /v1/memory/graph`
- query param:`entities`(逗号分隔,side agent 提炼的)、`agent_id`、`scope`、`top_k`(默认 8)
- 内部:用 entities 命中 KG 实体 → RetrieverAgent 召回(memory×score)+ 神经场扩散路径 + 蝴蝶翼联想 → 组装图

**响应 schema**:
```jsonc
{
  "query_entities": ["React", "性能优化"],          // 回显 side agent 提炼的
  "nodes": [
    {
      "id": "mem_xxx | ent_yyy",                    // memory_id 或 entity_id
      "kind": "memory | entity",
      "content": "记忆内容或实体名",
      "memory_id": "mem_xxx",                       // kind=entity 时关联回 memory
      "lif_activation": 0.73,                       // 神经场激活值(neural_field field)
      "match_score": 0.85,                          // RetrieverAgent.match_score
      "composite_score": 0.78,                      // match × lif
      "origin": "foreground | agent",
      "state": "active | stale | archived",
      "scope": "agent",
      "wing": "forward | backward | none"           // 蝴蝶翼激活类型
    }
  ],
  "edges": [
    {
      "src": "node_id", "dst": "node_id",
      "rel": "kg_relation | lif_spread | butterfly_assoc",
      "weight": 0.42,
      "label": "关系名(kg_relation 时)"             // 可选
    }
  ],
  "activated_path": ["ent_a", "ent_b", "mem_x"],    // LIF 扩散激活路径(有向)
  "meta": {
    "agent_id": "...", "scope": "...",
    "lif_snapshot_id": "...",                       // 神经场快照(可追溯激活态)
    "total_nodes": 12
  }
}
```

**组装来源**(全从内部已算的中间结果,不新增打分逻辑):
- `lif_activation` ← neural_field `field[concept]`(NeuralState)
- `match_score` ← RetrieverAgent.match_score
- `composite_score` ← match_score × lif_weight(现 retrieve 排序已用)
- `wing` ← ButterflyRecallStrategy 的 wing 过滤结果
- edges `kg_relation` ← `KnowledgeGraph.get_entity_relations`
- edges `lif_spread` ← `_kg_neighbors` 扩散路径
- edges `butterfly_assoc` ← ButterflyWing 双向联想(forward/backward)
- `activated_path` ← neural_field `_spread` 扩散步序
- `origin/state` ← _mem_to_dict 已透出(d7823d7)

**红线守卫**:图组装**只读**消费中间结果,不触 `match_score × lif_weight` 排序权重、不动 `permissions.py`、不改蝴蝶翼/神经场算法。

---

## 3. 各侧改动

### agent-os-v2 侧(阶段 1)
- 新增 `GET /v1/memory/graph`(`api/routes/memory.py`):
  - 接 `entities`/`agent_id`/`scope`/`top_k`
  - 调 RetrieverAgent 拿 ranked items(含 match_score/lif_weight)+ KG 实体邻居 + 蝴蝶翼 wing + 神经场 field/snapshot
  - 组装 nodes/edges/activated_path/meta
  - scope 过滤(d7823d7 候选层已支持)+ origin/state(d7823d7 已透出)
- RetrieverAgent 可能需暴露中间结构(当前 `retrieve` 只返回 ranked [{item,score}],需扩展返回 match/lif/kg_neighbors 明细供图组装 —— **设计点:扩展返回 vs 图端点内部重算**,优先扩展返回避免重算)

### openclaw 侧
**阶段 0**(执行 session):
1. `adapter.ts` HttpBackend.recall 补传 scope(并新增 `recallGraph(entities)` 方法调 /memory/graph)
2. `RecallRouteRow`/`recallRouteRowToItem` 解析 origin/state(去硬编码 foreground)
3. `types.ts` 补 state 字段 + 新增 `MemoryGraph`/`GraphNode`/`GraphEdge` 类型
4. ~~`formatRecallBlock` 渲染~~ **暂停**(阶段 3 图渲染)
5. `engine.ts` scope 可配 + `plugin.json` memoryScope

**阶段 2**(side agent 实体提炼):
- 新增 `memory/entity_extractor.ts`:用 `runtimeContext.llm.complete`(简单模型,prompt:从用户 turn 文本提炼 3-5 个实体关键字,JSON 数组)
- 模型:openclaw 配的便宜/快模型(或复用 agent-os-v2 的 glm-4-flash,走 HTTP)
- 输出:`string[]` 实体列表

**阶段 3**(异步流水线 + 图渲染):
- `engine.ts` 新增 per-turn 异步召回编排:
  - turn N `afterTurn` 或 `assemble`:fire-and-forget 触发 `extractEntities(prompt) → recallGraph(entities) → 缓存到 pendingReminderCache[sessionKey]`(不阻塞当轮)
  - turn N+1 `assemble`:`buildMemoryAddition` 先 drain `pendingReminderCache`(类似 spawnRecallCache),作为「记忆提醒」注入
- 跨 turn 缓存:复用 `spawnRecallCache`(engine.ts:101)模式,新 `pendingReminderCache: Map<sessionKey, MemoryGraph>`
- 图渲染:`formatReminderBlock(graph)` —— 把 nodes+edges+activated_path 渲染成结构化「记忆提醒」(关联记忆网络,非孤立条目),标注 origin/state/wing,注 system prompt cache-boundary 之后
- 异步时序:**严格下一轮**(turn N 召回 → turn N+1 注入)

---

## 4. 依赖 + 工作量

| 阶段 | 依赖 | 工作量(估) | 责任 |
|---|---|---|---|
| 0 底座 | 无 | ~1d(进行中,暂停渲染后更小) | openclaw(执行 session) |
| 1 图端点 | 无 | ~4d(承重:组装图 + RetrieverAgent 扩展) | agent-os-v2(新 session) |
| 2 实体提炼 | 无 | ~2d(side agent + 模型) | openclaw |
| 3 异步流水线 | 0+1+2 | ~4d(跨turn缓存 + 图渲染 + 编排) | openclaw |
| 4 联调 | 全部 | ~2d | 双侧 |

**总 ~13d**。0/1/2 可并行(0 在 worktree,1 在 agent-os-v2,2 可并入 worktree);3 依赖三者;4 收尾。

---

## 5. 验收(硬指标)
1. agent-os-v2 `GET /v1/memory/graph` 返回 nodes/edges/activated_path,含 lif_activation/match_score/wing,scope 过滤生效
2. openclaw side agent 提炼实体(非首句截取),实体质量明显优于 normalizeKeyword
3. 异步时序:turn N 提炼+召回不阻塞当轮响应;turn N+1 注入「记忆提醒」
4. 图渲染:LLM 能看到记忆间关联(节点+边),非孤立平铺条目
5. 多 agent 隔离:不同 agent_id/scope 的图互不可见
6. 红线回归:蝴蝶翼/信任域/五维/origin provenance 单测全过;openclaw cache-boundary 不破坏
7. origin/state 在图节点正确标注(foreground vs agent / stale 过期提醒)

---

## 6. 风险
1. **RetrieverAgent 扩展返回**(阶段 1 设计点):当前 `retrieve` 只返回 ranked 列表,图组装需 match/lif/kg_neighbors 明细。扩展返回可能动到 RECALL hook 契约 → 优先"扩展可选字段"而非改签名,或图端点内部独立调底子算(重算成本)
2. **图体积**:nodes+edges 可能膨胀(大 KG),需 top_k 限制 + 激活阈值过滤(只保留 lif_activation > threshold)
3. **异步时序竞态**:turn N 召回未完成 turn N+1 就到 → 缓存 drain 策略(等到完成或跳过本轮)
4. **side agent 成本**:每轮一次 LLM 提炼 → 用最便宜模型 + 只对足够长 prompt 触发(短 prompt 跳过)
5. **「记忆提醒」注入体积**:图渲染可能比平铺大 → 纳入 tokenBudget 预算(context-engine types.ts 有 tokenBudget)

---

*配套:《integration-spec.md》P0-P4 已完成 + 接入 spec Gap3/4 → 本 spec(召回架构升级,阶段 0-4)。阶段 0 = 接入 spec 的 openclaw 单侧消费底座。*
