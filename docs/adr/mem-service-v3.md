# ADR: mem-service-v3 阶段1(adapter + LIF 五维 + score 调参)
Date: 2026-08-06
Status: Active
Iteration Base: d7f8058(v2 P3-fix HEAD)
Builds on: docs/adr/mem-service-v2.md(ADR-8/2v2/4v2/9 upheld)

## ADR-5b: LLM 抽取 adapter + 蝴蝶翼(supplements ADR-5, 正则 fallback 保底)
Status: Accepted
Context: v2 extractor.py 纯正则(ADR-5), 纯中文裸句零命中(无引号/拉丁/谓词)。grill 实证 score 调参对中文 synonym/rewrite 盲区无解(match_item 空格分词 m=0)。LLM 抽取是 ingest 侧中文盲区真解。adapter 是 蝴蝶翼 + KG init bootstrap + autoDream 增量 三处复用点。
Decision: **LLMProvider Protocol**(`extract_facts(text) → {facts[], confidence, source_meta}`) + **CCRProvider 首版**(复用 127.0.0.1:3456 ccr 路由, 本机已部署) + claude-api/LMstudio 备。**蝴蝶翼** = N 路调用(prompt 变换 / 多 provider 交叉验证, N=3 默认) + 投票裁决(majority/quorum) + confidence 聚合(max/mean)。**fallback** = adapter 不可用/低 confidence → 回退正则 extractor(ADR-5 保底)。adapter 独立 node, 蝴蝶翼/init/autoDream 依赖。
Alternatives: (a)直连 ccr 无 adapter(provider 切换/蝴蝶翼多路受限); (b)全 LLM 无正则 fallback(不可用即崩)。
Consequences: ingest 加 LLM 调用(延迟 ms→s, cost); adapter 抽象层(provider 无关 + 蝴蝶翼 + 降级); 正则保底(LLM 不可用回退); ADR-5 upheld(正则作 fallback 非 supersede)。
Constrains: [T-adapter, Node Adapter]

## ADR-8v2: LIF 五维合成(supersedes ADR-8 标量 LIF 语义, decay 收编 recency)
Status: Accepted
Context: v2 LIF 单列标量(0.5 默认, decay 衰减), 不含"事实重要性"信号——被反复召回的关键 fact 与从未触的 fact LIF 相等(只要 fact_type+created_at 同)。召回强化路径缺失(终设/AO2 红线"五维 ImportanceScorer 非 LLM 合成")。
Decision: **五维(全非 LLM, 确定性可单测)**: freq=`1-exp(-access_count/5)`(召回饱和) / recency=`exp(-ln2·age_h/half_life_h)`, age_h=now-**last_accessed_at**(非 created_at, 召回刷新=强化路径, 吸收原 decay) / spread=`min(1, distinct_sessions/5)`(跨 session) / coherence=`1-conflicts/max(1,neighbors)`(同 subject 邻居一致比, 硬编码矛盾对) / source=`SOURCE_WEIGHT[extractor]`(regex=0.4/llm=0.7/human=0.9/vote=0.85)。**合成** LIF=`w_f·freq+w_r·recency+w_s·spread+w_c·coherence+w_o·source`(w_f=0.25/w_r=0.30/w_s=w_c=w_o=0.15)。decay 公式收编进 recency 维(原 ADR-8 decay `original_lif*0.5**(Δt/h)` 语义由 recency 接管, last_accessed_at 召回刷新)。LIF<0.1→deprecated 阈值作用在合成 LIF(不变)。`original_lif` 列语义变: decay 基准→source 维初值快照。schema +8 列(5 维 + access_count + last_accessed_at + seen_sessions)。
Alternatives: (a)保标量 LIF(无重要性信号, 召回强化缺失); (b)LLM 评分 LIF(非确定, AO2 红线要非 LLM)。
Consequences: LIF 成信任域+重要性合成; γ·LIF 有区分力; 召回强化反馈环; decay 双重计时风险(必用 last_accessed_at); coherence 开销(consolidate 算一次, recall 只刷 freq/recency/spread); SOURCE_WEIGHT 启发式(评测调参)。
Constrains: [T-lif-schema, T-lif-scorer, T-lif-wire, Node LIF-Schema/Scorer/Wire]

## ADR-4v2 增量: score 调参实证(grid search + baseline 驱动)
Status: Accepted(增量 ADR-4v2)
Context: v2 score α=0.5/β=0.3/γ=0.2 默认未论证("tunable as baseline firms up")。grill 实证: match_item 空格分词, 中文 synonym/rewrite m=0, **调参对盲区无解**; 只 positive 排序 + abbr 组有效。盲区真解=蝴蝶翼 LLM(ADR-5b)。
Decision: eval_recall 扩 query(abbr≥8 / syn≥10 / rewrite≥10 + positive 对照); α/β/γ grid search(或 LIF 五维权重 w_f..w_o); 跑 v2 对照基线(补 spec Node-I 缺口) + 调参后对比(hit@k)。不改 schema, 只调 scoring.py 常量 + eval_recall.py 扩。**硬约束写入 ADR**: 调参收益面=positive 内部排序 + abbr; synonym/rewrite 盲区靠 vec/LLM 非权重。
Alternatives: 不调参(默认值未论证)。
Consequences: 默认 α/β/γ 论证(或确认默认); baseline 量化调参收益; ADR-4v2 调参实证段兑现 "tunable as baseline firms up"。
Constrains: [T-score-tune, Node ScoreTune]

### 调参实证落地(Node E, orch-worktree-E)

实施结果(2026-08):
- **scoring.py 参化**: `score_fact(weights=(α,β,γ)|None)` + `compute_lif(lif_weights=dict|None)`
  可选参, None 回落模块常量(`ALPHA_MATCH=0.5/BETA_CENTRALITY=0.3/GAMMA_LIF=0.2` /
  `LIF_WEIGHTS` dict)。透传链: `cli.recall → recall.recall → scoring.score_fact`。
  不改 schema, 不改默认行为(向后兼容)。
- **eval_recall.py 扩**: 15 query 组 / 48 条(abbr=8 / syn=10 / rewrite=11 / positive=19);
  `GRID_WEIGHTS`(8 点: 默认 + 3 角点 + 4 偏置) grid search; `__main__` 打印
  baseline(default) vs tuned(best hit@5) 对比表 + 角点盲区印证。
- **硬约束印证(grid 角点)**: weights=(1,0,0)/(0,1,0)/(0,0,1) 三角点下,
  synonym/rewrite hit@5 恒 = 0(因 `match_item` 空格分词 m=0 → score=0 → 排不进 top-k;
  centrality/LIF 角点因候选集不含盲区事实同样 0)。**确认: 调参对 synonym/rewrite
  盲区无解(m=0 是字面层, 权重再怎么调也变不出非零项)**。调参收益面 = positive 内部
  排序 + abbr(字面子串重叠)。
- **盲区真解非权重**: synonym/rewrite 盲区靠蝴蝶翼 LLM 抽取(ADR-5b, ingest 侧产
  value 含近义)或 v3 向量层(query embedding 召回), 不在 scoring 权重调参范围。

验收: 6 项全过(scoring weights 参化 / eval_recall abbr≥8 syn≥10 rewrite≥10 /
pytest tests/eval_recall.py passed(含 grid) / `python tests/eval_recall.py` 打印
对比表 / 本段硬约束记 / e2e 不破)。

