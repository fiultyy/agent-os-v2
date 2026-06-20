# Agent OS 记忆内核设计 — 智能化重构蓝图

> 综合 P0–现状诊断 + mem-search 调研 + 认知科学（扩散激活）的设计。本文是后续 Part 1/2 实现的**权威依据**，避免设计在对话里散落。
>
> 适用场景：**个人本地化、小规模、持久化 agent 内核记忆**。LLM 成本先不考虑（基础推理，后续可换定向本地 LLM）。

---

## 0. 现状诊断（为什么重构）

当前记忆系统是「**机械记忆管理**」：
- **召回**：靠相关性（kw 子串 + KG LIKE），不靠重要性；对外 API 只开全量列表（无 query、无 importance 排序）
- **精炼**：靠机械规则（时间阈值/五维评分/Jaccard 重叠/regex），**不读内容语义**
- **KG**：regex 抽取，质量低（中文/隐含关系全漏），不是语义理解
- **LLM 介入**：只在①对话本身 ②task_consolidator（任务后孤立可选点）；**记忆流转本身零 LLM**
- **纸面能力**：蝴蝶翼（butterfly_wing.py）、LIF（kairos.py）代码完整但**未通电**——且研判发现它们是**空壳骨架**（relevance 硬编码、associations 拼接、LIF 注入是元信息），不是"接个线就能用"

**结论**：要真正"召回重要内容 + 精炼成知识 + 可靠关系图"，三样都缺一个 **LLM 语义层**。

---

## 1. 目标：智能记忆内核

把记忆从「机械管理工具」升维成「**agent 的自我**」：
- **side agent 驱动的 LLM 记忆管线**（Part 1）——每环节一个 LLM agent 按常规推理整理
- **神经状态场**（Part 2）——LIF 电位场 + 蝴蝶翼扩散，提供注意力 + 人格
- **召回计算引擎**——确定性加权计算（match × lif_weight），程序化输出，解耦请求方
- **身份召回闭环**——agent 身份由记忆涌现，非 prompt 硬编码
- **个人本地化持久内核**——轻量 SQLite + 本地 LLM + 信任域隐私

---

## 2. 数学原理：扩散激活（Spreading Activation）

整个设计的数学骨架 = **加权语义网络上的扩散激活**（认知科学 50 年标准联想召回模型）：

```
LIF 场:    V: concept → ℝ⁺         (连续电位状态,持久,漂移,有回退快照)
KG 图:     G = (概念节点, 蝴蝶翼边)  (结构骨架)
蝴蝶翼:    边权重 w(c',c)           (联想强度, Hebbian/LTP 生长)
召回:      种子→图扩散→排序组合     (spreading activation, 状态依赖)
身份:      长期场吸引子              (高频模式沉淀 = 人格)
生长:      LTP(权重)+加节点(图)+baseline(场) 随经验演化
```

**三要素对应**：LIF=激活状态 / KG=网络结构 / 蝴蝶翼=边权重。**与大脑联想召回一致**（扩散激活理论，Collins & Loftus 1975）。

---

## 3. Part 1：核心记忆管线（4 个 LLM side agent）

复用现有骨架（event bus P1 / store / KG schema / 4 层 / 状态机 / 五维 / 信任域），**新增 4 个 LLM side agent**，每个负责一个环节：

| Side Agent | 职责 | 触发 hook | 降级（LLM 失败） |
|---|---|---|---|
| **① IngestorAgent** | 原文 → LLM 抽实体-关系入 KG + 五维评分 + **身份维度标签** | `on_ingest`（store 后 async） | 回退 regex KG + 默认分 |
| **② ConsolidatorAgent** | episodic → semantic **理解后合并提炼**（非机械去重） | `on_consolidate`（SESSION_END/定时） | 回退机械 reflect（Jaccard） |
| **③ RetrieverAgent** | 召回计算引擎：`match × lif_weight` → 排序组合 | `on_recall`（recall 请求） | 回退 kw+KG 相关性 |
| **④ CuratorAgent** | 离线质检：归档/合并重复/纠错（对标 hermes curator） | `on_curate`（24h 定时） | 回退 state_pruner+forgetting |

**解耦**：每个 agent 是 event bus 上的 hook（P1 总线已有），失败自动降级到现有确定性兜底。**Part 1 独立可用，不依赖 Part 2**。

---

## 4. Part 2：神经状态场（LIF + 蝴蝶翼融合）

把蝴蝶翼/LIF 从"两个孤立空壳工具"融合成**一个神经状态场子系统**，给 Part 1 side agent 提供「注意力指针 + 人格积累」。

### 4.1 复合状态组（必定一组）
```
NeuralState(agent_id) = {
  field:            {concept: potential}   # 连续层:电位场(漂移)
  attention_group:  [memory_ids]           # 离散层:场投影(高电位概念→记忆)
  baseline:         {concept: resting_pot} # 稳态基线(防漂移失控)
  turn_id, updated_at
}
```
- `field`（连续）：概念电位分布，每 turn 漂移
- `attention_group`（离散）：top-N 高电位概念 → KG/蝴蝶翼关联记忆 = 当前关注记忆群
- **两者一组**：field 驱动漂移/扩散/固化；attention_group 直接喂 RetrieverAgent

### 4.2 连续漂移（每 turn 三步，非离散 fire）
```
① 激活: 当前内容相关概念 +Δpotential
② 漏电: 全概念 ×(1-decay),向 baseline 回归(不归零)
③ 扩散: 蝴蝶翼联想传播(概念→联想概念电位联动)  # 蝴蝶翼 = 扩散路径
```

### 4.3 鲁棒性 + 回退点（连续漂移的安全网）
1. **快照回退点**：定期存 `(field, attention_group)` 快照，版本链，`restore(snapshot_id)` 可回滚
2. **异常检测 + 自动回退**：电位爆炸（单概念>阈值 N turn）/ 场塌缩（全 0）/ 注意力骤变（>80%）→ 回退上个 stable 快照
3. **稳态基线（homeostasis）**：长期无激活 → 漏电回归 baseline（不归零）；baseline 缓慢学习（LTP，高频概念基线上调 = 人格沉淀）

### 4.4 双层产物
| 层 | 产物 | 消费者 |
|---|---|---|
| **即时（注意力）** | top-N 高电位概念 → attention_group | RetrieverAgent 召回加权 |
| **长期（人格化）** | 高频累积概念 → 固化 semantic + 稳定偏好 | ConsolidatorAgent 固化 + 身份召回 |

### 4.5 蝴蝶翼的真用途
蝴蝶翼 = **KG 图上的扩散边权重**（联想传播路径），**不是**给单条记忆评分的工具。relevance 该是图上学出来的边权重，不是硬编码常量。

---

## 5. 召回计算引擎（解耦请求方）

召回是**确定性加权计算**，不是模糊"涌现"：

```
recall(query, LIF_state) → 排序的 KG 元素关系组

for each KG 元素关系组 g:
    match_g  = match_score(g, query)        # query 与 g 的匹配分(数值)
    lif_g    = lif_weight(g, LIF_state.V)   # g 涉及概念在 LIF 场的加权(数值)
    score_g  = match_g · lif_g              # 召回分(确定数值)

return sort_by(score_g, desc)               # 排序的元素关系组(程序化结构)
```

- **数值永远在**：KG 数据 + LIF 电位都是数值，不存在"想不起"，只有"分数高低"（排序前后）
- **LIF 没激活 → lif_weight 小 → score 小 → 排序靠后**（数据还在）
- **输出程序化**：`[{kg_group, score}, ...]` 排序结构
- **解耦边界**：记忆系统只算 + 返回排序组合；**请求方怎么处理（LLM 综合/注入/取 top-K）是它的事，与记忆召回无关**

---

## 6. 身份召回闭环（记忆涌现身份）

agent 身份 = **记忆群召回涌现**，不是 prompt 硬编码。

### 6.1 记忆身份维度（IngestorAgent 抽取时打标签）
```
identity_category ∈ {IDENTITY, GOAL, TRAIT, KNOWLEDGE}
KG 实体类型扩展: identity / goal / trait / capability / knowledge
```

### 6.2 身份召回协议（全新 agent 来时标准请求）
```
GET /v1/identity?agent_id=X
→ {
    what_i_remember: [...],   # attention_group + KNOWLEDGE top
    who_am_i:        [...],   # IDENTITY 记忆 + LIF 人格核心
    my_goals:        [...],   # GOAL 记忆
    my_traits:       [...],   # TRAIT 记忆
    personality:     {...}    # LIF baseline 沉淀(慢变量)
  }
```

### 6.3 涌现（请求方处理，非记忆系统）
四组记忆群 → **请求方 agent** 的 LLM 现场综合成自我陈述 → 注入对话（替代 prompt 身份）。**记忆系统只交付记忆群，不综合**（解耦）。

### 6.4 闭环
```
记忆累积(Part1精炼 + KG结构化 + LIF场沉淀)
  → 持久化内核(IDENTITY/GOAL/TRAIT/KNOWLEDGE)
  → 全新 agent 来 → 身份召回(四问) → 记忆群
  → 请求方 LLM 涌现身份 → agent 对话/行动
  → 产生新经验 → 写入记忆 → 身份持续演化  ↺
```
agent 身份**长出来**，不是预设；换实例接同内核，身份延续。

---

## 7. 个人本地化设计

| 维度 | 设计 |
|---|---|
| **轻量** | SQLite（已有）+ 本地智谱 LLM（已有），单机，不引入 Neo4j |
| **隐私** | 信任域 5 级（已有，独门）+ 本地不外传 + 多项目隔离 |
| **小规模省成本** | 确定性先行（prune/状态机零成本）+ LLM 高价值节点（异步）+ **LIF 门控**（电位达标才巩固） |
| **持久化内核** | semantic = 内核知识（跨会话人格/稳定事实）；episodic = 经验；物理分离 |

---

## 8. 两部分依赖 + 落地顺序

```
Part 1（核心,独立可用）              Part 2（增强,依赖 Part 1）        身份闭环
  ①Ingestor(LLM 抽取+身份标签) ─┐
  ②Consolidator(LLM 合并) ──────┼─► 真实可用记忆内核 ─► ⑤神经状态场(LIF场+蝴蝶翼扩散) ─► ⑧身份召回接口
  ③Retriever(召回计算引擎) ─────┤                     ⑥鲁棒回退(快照+异常+基线)        ⑨LLM 涌现(请求方)
  ④Curator(LLM 策展) ───────────┘                     ⑦召回加权(match×lif_weight)
```

- **Part 1 必须先做**（1→4）——"真实可用"底线，每步独立降级，做完任意一步记忆都变强
- **Part 2 在 Part 1 后**（5→7）——神经状态场，骨架复用 + LLM 填充语义
- **身份召回**（8→9）——闭环终点，记忆涌现身份
- **Part 2/身份可弃**——故障时回退 Part 1 基础管线（kw+KG 照跑）

---

## 9. 关键设计原则（贯穿）

1. **side agent 是 LLM 推理单元**，每环节一个，"看内容→理解→结构化"
2. **神经状态场是 side agent 的认知层**（非独立子系统），LIF 提供注意力/人格，蝴蝶翼提供扩散
3. **召回是计算引擎**（match×lif_weight），程序化输出，解耦请求方
4. **全链路降级**：每个 agent/工具/状态场失败 → 回退确定性兜底，永不让系统挂
5. **event bus 解耦**：每个 agent 是 hook，可独立开关（feature gate）
6. **复用骨架**：4 层/状态机/五维/信任域/event bus/KG schema/LIF 数学/蝴蝶翼骨架都复用，只补 LLM 语义填充

---

## 附：与 mem-search 调研的对齐

| | 借鉴 mem-search | agent-os-v2 独有 |
|---|---|---|
| 巩固 | hermes 两阶段（在线 background_review + 离线 curator） | **LIF 神经场门控**（时机感知） |
| KG | Cognee/Zep 的 LLM 抽取 | **蝴蝶翼扩散边**（联想召回） |
| 解耦 | hermes 事件总线 6 钩子（P1 已做） | **信任域隐私隔离**（个人本地刚需） |
| 框架 | 学界 short/long 二分（2 分） | **身份涌现闭环**（记忆=agent 自我） |

**定位**：mem-search 建议个人本地走 Mem0/Cognee 轻量 + hermes 两阶段。agent-os-v2 在此基础上多出**神经状态场 + 蝴蝶翼扩散 + 身份涌现**三独门——这是"个人 agent 内核"的差异化。
