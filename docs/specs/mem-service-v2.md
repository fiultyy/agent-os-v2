# Spec: mem-service-v2(decay + pagerank + baseline)
Date: 2026-08-06
Status: Draft
ADR: docs/adr/mem-service-v2.md
Iteration Base: 60fea34
Builds on: docs/specs/mem-service.md(v1, upheld)
QA Available: false(记忆服务非 web app, 全 general_test)

## 1. Problem Statement
v1 mem-service green/deployed, 但 grill 实证 2 空缺: (1) Fact reified 含 fact_type/valid_to/LIF 列(ADR-3)但无衰减写入方, ADR-3 全生命周期的衰减环空缺; (2) ADR-2 原设计 SQLite+networkx, v1 ponytail 砍了 networkx, 聚合度(centrality)重排缺; (3) 中文同义/省称/改写召回三组全 miss(rust 命中, 铁锈/rusty/开发语言 全[]), 无量化基线。v2 补 decay + pagerank + 评测基线(零外部依赖, networkx 已装)。

## 2. Solution (In-Scope)
- **decay**(consolidate.py 加 decay pass): LIF *= 0.5**(Δt/half_life), half_life ephemeral=7d/stable=90d/permanent=∞; ingest `--fact-type` flag 指定 fact_type(默认 stable); LIF<0.1 → status active→deprecated
- **pagerank**(scoring.py 加 centrality + recall.py on-the-fly networkx 图构建): recall 时建 networkx 图(entity+fact 边) + pagerank centrality, score = α·match+β·centrality+γ·LIF(α=0.5/β=0.3/γ=0.2)
- **baseline**(tests/eval_recall.py): 30-50 中文 fact + 10 组同义/省称/改写 query hit@k, 量化 v1 盲区 + decay/pagerank 效果
- ingest `--fact-type` flag(cli.ingest + cli.py ingest 子命令加参数)
- consolidate 触发 decay pass(consolidate 时先 decay 再 dedup)

## 3. Out-of-Scope (Non-Goals)
- 向量实体tag + embedding 召回(等数据 100+ fact + baseline 量化)— v3 defer
- UserPromptSubmit/PreCompact 三频 hook(outward 改 settings, 独立可后)— defer
- LLM 抽取/蝴蝶翼(等召回 baseline)— defer
- autoDream daemon(依赖 decay, 后续)— defer
- 冷层类聚(依赖向量)— defer
- ingest 增量维护 pagerank 图(on-the-fly 够 MVP)— defer

## 4. User Stories / Scenarios
1. As CC, I want `mem ingest "X" --fact-type ephemeral` 标临时 fact, so that 7d 后衰减
2. As CC, I want `mem consolidate` 触发 decay + dedup, so that 老 fact LIF 衰减/重复合并
3. As CC, I want recall 排序含 centrality(高连通 entity 优先), so that KG 核心 fact 排前
4. As dev, I want `pytest tests/eval_recall.py` 量化召回命中率, so that decay/pagerank 效果可对照
5. GIVEN ingest "用户使用 rust" stable + consolidate 90d 后 THEN LIF *= 0.5(decay 生效)
6. GIVEN 高连通 entity(多 fact 关联) WHEN recall THEN centrality 高排前

## 5. Implementation Decisions
- decay: consolidate.py decay pass + ingest --fact-type [ADR-8]
- pagerank: scoring.py centrality + recall.py networkx on-the-fly [ADR-2v2]
- score: α·match+β·centrality+γ·LIF [ADR-4v2]
- baseline: tests/eval_recall.py hit@k [ADR-9]

## 6. Testing Decisions
- Seams: cli(ingest --fact-type/consolidate) + recall(centrality) + eval_recall.py
- decay: consolidate 后 LIF 衰减断言 + status 流转
- pagerank: recall centrality 非零 + score 排序含 centrality
- baseline: eval_recall.py hit@k 量化

## 7. Acceptance(关联编排图节点)
- [ ] Node G: decay(consolidate decay pass + ingest --fact-type + status active→deprecated) → general_test
- [ ] Node H: pagerank(scoring centrality + recall networkx on-the-fly + score 加权) → general_test
- [ ] Node I: baseline(tests/eval_recall.py hit@k 量化 v1 + v2) → general_test
- [ ] P3 Regression: v1 e2e(tests/test_e2e.py 6 passed 不破) + eval_recall baseline 对照
- [ ] ADR Compliance: ADR-8/2v2/4v2/9 upheld + v1 ADR-1..7 不破

## 8. Open Issues
(空 — grill 已收敛范围 + 2 MUST-ASK 决策已定)

## 9. Defer 预判
- 向量实体tag + embedding(数据 100+ + baseline 量化后)— v3
- UserPromptSubmit 注入(outward settings, 独立可后)
- LLM 抽取(等召回 baseline 决定)
- autoDream daemon(依赖 decay 落地后)
- ingest 增量 pagerank(on-the-fly 性能不足时)
