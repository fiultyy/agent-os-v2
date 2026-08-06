# ADR: mem-service-v2(decay + pagerank + baseline)
Date: 2026-08-06
Status: Active
Iteration Base: 60fea34(v1 P0 skill deploy fix 后 main HEAD)
Builds on: docs/adr/mem-service.md(v1 ADR-1..7 upheld, 本文件仅增量)

## ADR-8: type-aware decay(Fact LIF 衰减 + status 流转)★
Status: Accepted
Context: v1 Fact reified 含 fact_type/valid_to/LIF 列(ADR-3)但**无衰减写入方**(grill 实证 fact_type 全默认 stable, store.put_fact 默认 stable, extractor 不传), ADR-3 全生命周期(回溯/衰减/矛盾链)的衰减环是 v1 唯一空缺。
Decision: consolidate 加 decay pass。**公式**: LIF *= 0.5**(Δt/half_life)(指数衰减, Δt=now-created_at)。**half_life 表**: ephemeral=7d / stable=90d / permanent=∞(不衰减)。**fact_type 写入**: ingest `--fact-type` flag 手动指定(默认 stable, 自动 ingest 落 stable)。**status 流转**: decay 后 LIF<0.1 → status active→deprecated(补 v1 只 active+superseded 的三态转换, schema status 已支持)。**decay 进 score**: 经 LIF 自然进 recall score(ADR-4v2 澄清"LIF 含衰减", 不另加 decay 维)。decay 单向不可逆但 consolidate idempotent + supersedes 软删可追溯。
Alternatives: (a)extractor 正则推断 fact_type(判 fact 生命周期不准); (b)全默认 stable 不区分(decay 不分 half_life, 无 type-aware)。
Consequences: LIF 从写死标量变会老化的信任域; fact_type 手动 flag 准确但依赖用户/CC; ephemeral/permanent 需显式指定否则全 stable。
Constrains: [T-decay, G]

## ADR-2v2: SQLite + networkx, recall on-the-fly 图构建(supersedes ADR-2 networkx defer)
Status: Accepted
Context: ADR-2 原设计"SQLite+networkx", v1 ponytail 砍了 networkx(图遍历/聚合度 defer, grill 实证源码无 networkx import)。v2 回补 centrality(pagerank)。
Decision: recall 时从 SQLite 构建 networkx 图(entity 节点 + fact 边, status=active) + `networkx.pagerank` 算 centrality。**on-the-fly**(每次 recall 重建, 无 ingest 维护, N fact 性能 MVP 可接受)。图不持久化。
Alternatives: ingest 增量维护图 + 增量 pagerank(复杂, 增量算法 + 图持久化 + ingest 开销)。
Consequences: recall 加图构建开销(O(V+E) per recall); centrality 入 score 重排(ADR-4v2); networkx 3.6.1 已装(零新依赖)。
Constrains: [T-pagerank, H]

## ADR-4v2: score 加权融合(match + centrality + LIF)(supersedes ADR-4 score=match×lif)
Status: Accepted
Context: ADR-4 v1 scored=match×lif(乘积, "不上 centrality/向量 MVP")。v2 加 pagerank centrality(ADR-2v2), 乘积公式不兼容多维权加(centrality=0 抹杀其他维)。
Decision: **score = α·match + β·centrality + γ·LIF**(加权, 非乘积)。默认 α=0.5/β=0.3/γ=0.2(可调, 后续评测基线 ADR 增量调参)。decay(ADR-8)via LIF 自然进 score。centrality = pagerank(entity) 归一化 [0,1]。match = v1 match_item(不变, token 拆分命中比)。
Alternatives: (a)乘积 match×lif×centrality(centrality=0 抹杀); (b)完整公式 α·vec+β·centrality+γ·LIF−δ·decay(vec/独立 decay 维 defer)。
Consequences: score 可解释性下降(三维 vs v1 二维); --verbose 加 centrality 字d; ADR-4 v1 "不上 centrality(MVP)" 推翻(v2 显式上)。
Constrains: [T-pagerank, H]

## ADR-9: recall 评测基线(量化中文盲区)
Status: Accepted
Context: grill 实证 v1 中文同义/省称/改写召回三组全 miss(rust 命中, 铁锈/rusty/开发语言 全[])。无量化基线, decay/pagerank/向量效果无对照。
Decision: tests/eval_recall.py 评测集: 30-50 中文 fact(覆盖技术词/中英混排/纯中文裸句) + 10 组同义/省称/改写 query, hit@k(k=3/5) 命中率。量化 v1 baseline + decay/pagerank 后对比(回归门)。
Alternatives: 无评测集(凭感觉, 无法量化改进)。
Consequences: 评测集人工造数据(偏差); hit@k 命中率作 P3 regression 对照; 基线驱动后续向量层/LLM 抽取决策。
Constrains: [T-baseline, I]
