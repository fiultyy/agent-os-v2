# 记忆内核实施 Plan（Part 1 + Part 2）

> 基于 [`memory-kernel-design.md`](./memory-kernel-design.md) + workflow 规划（7 agent）+ 三视角对抗审查修正。
> 这是**修正后**的可执行实施蓝图——已吸收审查的 critical/high 修正。

---

## Part 1：核心记忆管线（4 LLM side agent）

### Step 0：公共底座（硬前置，最先做）
- `event_bus.py` EventType 扩 `INGEST/CONSOLIDATE/RECALL/CURATE` + `hooks.py` 4 个 Context dataclass + 统一 `SideAgentResult`
- ⚠️ **审查修正**：4 个 side agent hook 必须 `register(hook, EventType.INGEST)` **显式指定事件**，不走默认全量挂载（否则每 hook 收 10 事件无效分发）
- 降级：`MEMORY_EVENT_BUS_ENABLED=0` → OBSERVER hook 全跳过 = 现状确定性管线
- files: `event_bus.py`, `hooks.py`, `__init__.py`

### ① IngestorAgent（`on_ingest`：原文 → LLM 抽实体-关系入 KG + 五维评分 + identity_category 标签）
- 复用 task_consolidator 骨架 + `kg.add_entity/add_relation`（schema 不动）
- 新写 `_INGEST_PROMPT`（LLM 输出 JSON：entities/relations/importance 五维/identity_category）
- 降级：`kg.extract_and_ingest`（regex）+ `scorer.score`
- **P0 红线**：`on_ingest` 头部按 `ctx.origin` 早返，FOREGROUND 记忆永不改
- ⚠️ **审查修正**：`identity_category` 标签**在此同体实现**（为第 6 章身份召回铺数据，不能拖到⑧）
- ⚠️ **审查修正**：`_parse_ingest` 分层容错（任一字段缺失不致命）
- files: `sideline/ingestor_agent.py`(新), `chat.py`, `engine.py`, `default_hook.py`

### ② ConsolidatorAgent（`on_consolidate`/复用 `SESSION_END`：episodic → semantic 理解后合并）
- 复用 `reflect` 的 P0 过滤（`MemoryFilter(origin=AGENT, EPISODIC)`）+ BackwardWriter confidence 路由
- 新写 `_CONSOLIDATE_PROMPT`（LLM 输出 merged summaries + contradictions）
- 降级：`service.reflect()`（Jaccard 确定性合并）
- ⚠️ **审查修正**：复用 `db_watcher._lock_for(agent_id)` 与 migrate 互斥（session_end 路径同样需锁）
- files: `sideline/consolidator_agent.py`(新), `engine.py`

### ③ RetrieverAgent（`on_recall`：召回计算引擎 `match × lif_weight`）
- **Part 1 阶段 `lif_weight=1.0`**（纯 match 排序，Part 2 注入真 V）
- ⚠️ **审查修正（high）**：**不改 `service.recall` 内部**（service 当前不依赖 bus，改它破坏分层）。接入点在**路由层**（chat.py 调 recall 处先 `bus.emit(RECALL)` 取 OBSERVER 结果，None 则 fallback `service.recall`）
- ⚠️ **审查修正（high）**：`lif=1.0` 必须证明不劣于现状 UnifiedRecall（kw0.4+kg0.6）——**A/B 回归测试门控，默认关直到验证**（唯一可能让系统变差的 agent）
- ⚠️ 预留 `lif_weight(g, V)` 注入点（Part 2 无侵入升级）
- files: `sideline/retriever_agent.py`(新), 路由层

### ④ CuratorAgent（`on_curate`：离线 LLM 质检 归档/合并/纠错）
- 降级：`state_pruner.prune` + `active_forgetting.run_sweep`
- ⚠️ **审查修正（medium）**：**不插入 `run_maintenance` 同步链**（该链是 Zero-LLM 契约）。在 Lock 释放后**独立 `create_task(curate)`** fire-and-forget
- files: `sideline/curator_agent.py`(新), `db_watcher.py`, `engine.py`

---

## Part 2：神经状态场（LIF + 蝴蝶翼融合）

### ⑤ NeuralState 状态场（地基）+ ⑥ 鲁棒回退（安全网）—— ⚠️ 合并交付
> **审查修正（high）**：⑤⑥ 必须合并为一个交付单元——连续漂移本身会数值漂移，没有⑥的异常检测+快照回退就是裸奔，违背"全链路降级"红线。

**⑤ 数据结构 + 漂移**：
- 新建 `neural_field.py`：`NeuralState(agent_id, field: dict[concept→potential], attention_group, baseline)`（**不继承 LIFState**——维度不同，单标量 vs per-concept 字典）
- 持久化 `NeuralFieldStore`（SQLite）+ 漂移三步（激活/漏电向 baseline 回归/蝴蝶翼扩散）
- ⚠️ **审查修正（critical）**：**扩散方程必须归一化**——`field[neighbor] += field[c] * confidence * spread_rate / out_degree(c)`，且 `spread_rate + Σconfidence/out_degree ≤ 1`（谱半径约束），否则加性传播在无向 KG 上**发散或退化**
- ⚠️ **审查修正**：扩散只对 top-M（如 20）高电位概念做局部 1-2 hop（非全图 BFS，防每 turn 上千 KG 查询拖慢）

**⑥ 鲁棒回退**：
- 快照版本链（`neural_snapshot` 表）+ 异常检测（爆炸/塌缩/骤变）+ 稳态基线 LTP
- ⚠️ **审查修正（critical）**：异常阈值改**相对 baseline 倍数**（`field[c] > k*max(baseline[c], ε)`，非绝对值）+ 滞回（连续 ≥2 turn 才触发），否则扩散病态被伪装成状态异常反复抖动
- ⚠️ **审查修正（critical）**：`learn_baseline` 在**去扩散后的纯激活场**学习 + clamp `[0, baseline_max]`，否则漏电×扩散联合不动点漂移失控
- ⚠️ **审查修正**：环形缓冲快照（最近 K=20 + `is_stable` 永留），防版本链膨胀
- files: `neural_field.py`(新), `event_bus.py`/`neural_hook.py`, `test_neural_field.py`(新)

### ⑦ 召回加权 + 蝴蝶翼空壳填充
- **召回加权**：`match × lif_weight`（消费⑤的 field）
- ⚠️ **审查修正（high）**：`lif_weight` 用**相对排名分位**（rank-based，非绝对值）——否则 lif 长期低电位时 score≈match×0.1 被压制；用 `score = match^a * lif^b` 加权几何均值标定 a,b
- ⚠️ **审查修正（high）**：**score 对 memory item 计算**（非"关系组 g"）——`match_item(query,item) × lif_item(field, item.涉及概念)`；"组 g"概念废弃或仅用于扩散
- **蝴蝶翼改造**：`compute_forward_wing` relevance 从硬编码 0.5 → 读 KG `relations.confidence`（边权重）
- ⚠️ **审查修正**：只改 `compute_forward_wing` 局部取值，**不动类常量 `_RELEVANCE_WEIGHT`**（被 backward wing 复用，改了污染）+ 回归测试 `test_backward_wing_unaffected`
- files: `butterfly_wing.py`(改 225/233/613), `neural_field.py`(加 lif_weight 纯函数), `_recall/`(weighted_recall 新), 测试

---

## 修正后落地顺序（审查优化，最小化风险）

**原设计**：Part1(1-4) → Part2(5-7) 串行
**修正**：Part2 不真依赖 Part1 的 LLM agent（只依赖确定性兜底：regex KG 概念、scorer 评分、kw+KG 召回，这些已通电），**可部分并行**：

```
Step0(共用底座)
  → ①Ingestor + ⑤NeuralState 地基  并行
  → ②Consolidator
  → ⑥鲁棒回退(与⑤同体合并)
  → ③Retriever(预留 lif_weight 注入点)
  → ⑦召回加权 + 蝴蝶翼改造(注入真 V)
  → ④Curator
  → ⑧/⑨身份召回(①已铺 identity_category)
```

每个 step 自带 feature gate（默认关，灰度），任一失败降级到确定性兜底。

---

## 关键审查问题汇总（实现前必须解）

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 1 | **critical** | ⑤扩散方程缺归一化 → 发散/退化 | 能量守恒 `×confidence/out_degree`，谱半径 ≤1 |
| 2 | **critical** | ⑥异常检测绝对阈值 → 扩散病态伪装成异常反复抖动 | 相对 baseline 倍数 + 滞回 ≥2 turn |
| 3 | **critical** | ⑥baseline LTP 与扩散联合不动点漂移失控 | 去扩散场学习 + clamp |
| 4 | **high** | ③Retriever 改 service.recall 破坏分层 | 接入点移到路由层 |
| 5 | **high** | ③lif=1.0 可能劣于 UnifiedRecall | A/B 回归门控，默认关 |
| 6 | **high** | ⑦match×lif 量纲不对齐 | lif 用排名分位 / 加权几何均值 |
| 7 | **high** | ⑦"关系组 g"定义不明阻塞实现 | score 对 memory item 算 |
| 8 | **high** | Part2 假依赖 Part1 LLM → 过度串行 | 改部分并行（①⑤并行起步） |
| 9 | **high** | ⑤⑥拆分违背全链路降级 | 合并交付 |
| 10 | **high** | identity_category 拖到⑧ → 闭环无数据 | 在①同体实现 |
| 11 | medium | ④Curator 插 run_maintenance 破坏 Zero-LLM 契约 | 独立 fire-and-forget task |
| 12 | medium | Step0 hook 全量挂载 → 无效分发 | 显式 `register(hook, event)` |

---

## 验收基线（每 step）
- feature gate 默认关 → 现状零回归
- gate 开 → A/B 对比不劣于降级路径（尤其 ③⑦ 召回）
- P0 红线：FOREGROUND 记忆永不被 side agent 改（①②④ 都 filter origin=AGENT）
- 数学收敛：⑤漂移在测试用例下稳定（不发散/不塌缩）
