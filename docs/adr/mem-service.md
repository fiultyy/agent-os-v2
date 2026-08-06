# ADR: mem-service
Date: 2026-08-06
Status: Active
Iteration Base: 63e2049f81cdcb0efaa33a7a6c1a2cc92a8ea6e0

## ADR-1: 服务形态 — 独立 Python 服务 + cli + skill 接入
Status: Accepted
Context: 把前文确认的记忆流程落地,需独立进程可被 CC/AO2/多 agent 复用,且不改 CC 现有 MEMORY.md/memory md(叠加超集路线)。
Decision: 独立 Python 服务 `services/memory-service/`,暴露 cli 子命令(ingest/recall/consolidate,无 query);写 CC skill 包装 cli 接入。服务自治,CC 通过 skill+cli 调用,不嵌入 CC/AO2 进程。P2 默认在主实例内以 workflow(ultracode-workflow)+worktree 隔离执行 AO2 仓内代码;CC skill 源在仓内 `services/memory-service/skill/`(deploy 独立步骤 P4)。**本选择与 AO2 cc 是否经 orca-com 外部驱动无关——服务暴露的唯一面是 cli,任何 harness(主实例 workflow / AO2 自跑 / orca-com 远程)调同一 cli,执行载体正交。**
Alternatives: (a)嵌入 CC 进程(耦合改 CC);(b)嵌入 AO2 orchestrator(绑 AO2);(c)改 type 为 spawn+跨实例 return 桥(过度工程,服务已 cli 化)。
Consequences: 服务自带进程管理;cli 是唯一接入面(可测、可被任意 harness 调);skill 是 CC 专接入层。
Constrains: [T1, T5, E]

## ADR-2: 存储 — SQLite + networkx,Entity+Fact 表(无 MemoryItem)
Status: Accepted
Context: v1 lazy 起步,单机,零外部依赖。Fact reification 自包含(带 value/valid_from/LIF),不需 MemoryItem 中间层(AO2 的 MemoryItem 是为其 InMemoryStore 体系服务的包袱)。
Decision: SQLite(Entity + Fact 表 + source_refs)+ networkx(图遍历/聚合度,内存)。借鉴 AO2 "KG 导航定位" 思路(kg_recall.py:11-61),但召回对象是 **Fact 非 MemoryItem**。
Alternatives: (a)pgvector;(b)Neo4j;(c)保留 MemoryItem(为无人读的表加写路径,劣)。
Consequences: 单文件 DB 易备份;图遍历限单机(够 v1);Fact 表是核心。
Constrains: [T1, A]

## ADR-3: KG schema — Fact reification(升级 AO2 的 Relation 边属性)★核心
Status: Accepted
Context: AO2 的 Relation 是带 valid_from/valid_to/confidence 的边属性,非独立 fact 节点(无 fact_type/extractor/LIF/centrality/supersedes)。前文设计 fact 需全生命周期。
Decision: Fact 作为独立节点(reified),正交元数据: valid_from/valid_to(借鉴 AO2) + fact_type(ephemeral|stable|permanent) + LIF(信任域) + confidence + source_refs[](指 raw sessionId/leafUuid) + extractor(抽取溯源) + status(active|deprecated|superseded) + supersedes_id。Entity 简化 schema(id/name/entity_type/properties/created_at)——**删 source_memory_ids**(无 MemoryItem 致该字段失语义;entity→fact 关联通过 Fact.subject_id/object_id 反向,raw 溯源由 Fact.source_refs 承载)。Fact 自带 value 是内容载体,无 MemoryItem 中间层。**这是对 AO2 Relation 的结构升级,独立服务的核心价值点。**
Alternatives: 沿用 AO2 Relation 边属性(缺 fact 级生命周期)。
Consequences: Fact 表是核心;查询比边属性多一跳 join,换回 fact 全生命周期(回溯/衰减/矛盾链);F 节点 skeptic 须校验跨表 join 一致性。
Constrains: [T1, A]

## ADR-4: 召回排序 — scored=match×lif(match_item 抄, lif 读 Fact.LIF 标量)
Status: Accepted
Context: AO2 scored=match_score×lif_weight(weighted_recall.py: match_item 子串命中 :54-88, lif=信任域)。AO2 的 lif 源是运行时 NeuralField rank-based 分位(neural_field.py:335-366),mem-service 无运行时神经场。
Decision: v1 借鉴 scored=match×lif:match_item 子串命中逻辑直接抄 weighted_recall.py:54-88(零 LLM、可单测), **match 对 Fact.value 子串命中**(原算 MemoryItem.content, 改 Fact.value);lif 源改读 **Fact.LIF 存储列标量∈[0,1]**(与 AO2 NeuralField 解耦,不经 rank-based 分位——那是运行时场相对排名,对静态存储信任域不适用)。不上 centrality/向量(MVP)。decay 不进 recall score(随 consolidate 阶,见 ADR-6 defer)。
Alternatives: (a)v1 上完整公式 α·vec+β·centrality+γ·LIF−δ·decay(MVP 太重);(b)硬接 NeuralField(引入运行时神经场复杂度,defer)。
Consequences: v1 召回=字面/子串命中(英文/技术词够用;中文同义/省称受限,见 spec §9 defer);lif 加权来自 Fact.LIF 标量。
Constrains: [T3, C, F]

## ADR-5: 抽取 — 正则 EntityExtractor(7 英文谓词+中文同义+9 模式类,无 LLM)
Status: Accepted
Context: AO2 EntityExtractor(knowledge_graph.py:96-165)纯正则+CJK 中英混排,无 LLM,确定性。
Decision: v1 借鉴正则 EntityExtractor。英文谓词 7 个: is_a/uses/depends_on/contains/belongs_to/implements/connected_to;中文同义集: is_a=是|属于, uses=使用|采用|基于|调用, depends_on=依赖|需要, contains=包含|包括。实体模式类 9 个: CAPITALIZED_PHRASE/QUOTED_STRING/TECHNICAL_TERM/CAMEL_CASE/PASCAL_TECH/ALLCAPS_PASCAL/SNAKE_CASE/CJK_QUOTED(《》「」『』)/CJK_LATIN_MIX(中文锚定拉丁标识符)。每 fact 记 extractor="regex"。LLM 抽取/蝴蝶翼 defer。
Alternatives: v1 上 LLM 抽取(引入模型依赖,重)。
Consequences: 抽取能力限正则覆盖(英文/技术词/中英混排;**纯中文裸句零命中**);复杂语义 fact 后续 LLM 补;v1 确定性可测。
Constrains: [T2, B]

## ADR-6: 超集演进 — v1=P3 级(cli+skill+KG fact),其余 defer
Status: Accepted
Context: 全套五层栈 + 三频hook + 向量 + autoDream v1 太重。前文定超集演进 P0→P4。
Decision: v1 MVP = cli(ingest/recall/consolidate) + skill 接入 + KG fact 存储 + match×lif 召回(P3 级)。Defer: 三频 hook(UserPromptSubmit/PreCompact)、向量实体tag、冷层类聚、autoDream 后台巩固、**type-aware 衰减(需 per-type half_life + consolidate 触发设计,随 consolidate 阶)**、LLM 抽取/蝴蝶翼、query 独立 cli(调试用 recall --verbose)。
Alternatives: (a)全套 v1(重风险高);(b)仅 cli 骨架(recall 弱)。
Consequences: v1 可用闭环(ingest→recall via skill);CC 内联集成(三频注入)留后续;clear 分阶。
Constrains: [全]

## ADR-7: 不改 CC 现有记忆 — 叠加路线,skill 源仓内+deploy 独立
Status: Accepted
Context: CC 现有 ~/.claude/projects/*/memory/*.md 是即时派热/温层,工作良好。独立服务在其下叠加 KG fact 层,不替换。
Decision: 服务独立进程,数据在 `services/memory-service/data/`。CC skill **源在仓内 `services/memory-service/SKILL.md`(服务根, 与 cli.py 同级)**(P2 worktree 正常 commit),**deploy = 软链服务根** `~/.claude/skills/mem → services/memory-service/`(cli.py+SKILL.md 全入 skills/mem/, CC 发现 + cli 同目录裸 import 都 work)。CC 通过 skill+cli 读写服务 KG。**绝不改 CC ~/.claude/projects/*/memory/*.md**。skill 是唯一桥梁。(v1 原 skill/ 子目录 → P0 修移根, 因 CC 发现要 ~/.claude/skills/mem/SKILL.md 根 + cli 同级裸 import)
Alternatives: 替换 CC memory(破坏现有,违反约束)。
Consequences: 两套记忆并存(CC md 即时派 + 服务 KG fact);后续 skill 可选投影回 md(v1 不做)。
Constrains: [T5, E]
