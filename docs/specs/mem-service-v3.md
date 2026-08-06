# Spec: mem-service-v3 阶段1(adapter + LIF 五维 + score 调参)
Date: 2026-08-06
Status: Draft
ADR: docs/adr/mem-service-v3.md
Iteration Base: d7f8058
Builds on: docs/specs/mem-service-v2.md(v2 upheld)
QA Available: false

## 1. Problem Statement
v2 green 但 3 空缺: (1) LIF 标量不含事实重要性信号(召回强化缺失, 终设/AO2 红线要五维); (2) 正则抽取纯中文裸句零命中(synonym/rewrite 盲区, grill 证 score 调参无解, 需 LLM); (3) score α/β/γ 默认未论证。v3 阶段1 补 adapter(LLM 抽取 + 蝴蝶翼) + LIF 五维 + score 调参(零新依赖除 ccr 已部署)。

## 2. Solution (In-Scope)
- **adapter**(llm_provider.py + adapter.py): LLMProvider Protocol + CCRProvider 首版(127.0.0.1:3456) + claude-api/LMstudio 备 + 蝴蝶翼 N=3 路投票 + confidence 聚合 + fallback 正则; cli ingest 切 adapter; KG init bootstrap 复用(预留)
- **LIF 五维**(scoring.py + schema.sql + store.py + consolidate.py + recall.py): freq/recency/spread/coherence/source 合成 LIF; decay 收编 recency(last_accessed_at 召回刷新); schema +8 列; recall boost(召回强化)
- **score 调参**(scoring.py + tests/eval_recall.py): grid search α/β/γ(or LIF 五维权重) + eval 扩 query(abbr/syn/rewrite) + baseline 对比

## 3. Out-of-Scope (Non-Goals, v3 阶段2 后)
- PreCompact autoDream hook(outward settings, 阶段2)— defer
- 向量实体tag + embedding 召回(数据 100+ + 蝴蝶翼后)— v4
- KG init bootstrap 全量(adapter 预留, 全量抽取单独迭代)— defer
- autoDream daemon(PreCompact 阶段2)— defer
- LLM 抽取成本控制(caching/batch)— defer

## 4. User Stories / Scenarios
1. As CC, `mem ingest "用户偏好实证"`(纯中文裸句) → LLM adapter 抽 fact(正则漏的)
2. As CC, recall 多次命中某 fact → freq/recency 升 → LIF 升 → 排序浮顶(召回强化)
3. As dev, `pytest tests/eval_recall.py` 量化 v3 vs v2 baseline(hit@k 对比)
4. GIVEN 蝴蝶翼 N=3 路 LLM 抽取 WHEN 同 text THEN 投票裁决 + confidence 聚合
5. GIVEN adapter providers=[](无 LLM) WHEN ingest THEN fallback 正则(ADR-5 保底)

## 5. Implementation Decisions
- adapter: LLMProvider Protocol + CCR 首版 + 蝴蝶翼 N=3 + fallback 正则 [ADR-5b]
- LIF: 五维合成(freq/recency/spread/coherence/source) + decay 收编 recency [ADR-8v2]
- score: grid search α/β/γ + eval 扩 [ADR-4v2 增量]

## 6. Testing Decisions
- Seams: adapter(LLMProvider mock + ccr skipif) + LIF(compute_lif 单测五维边界) + score(eval_recall grid)
- adapter: mock provider + fallback 正则双路径; ccr 集成 skipif(未运行跳过)
- LIF: 五维边界(空邻居/全冲突/饱和) + recall boost(idempotent refresh)
- score: eval_recall grid search + baseline 对比表

## 7. Acceptance(关联编排图节点)
- [ ] Node Adapter: LLMProvider Protocol + CCRProvider + 蝴蝶翼 N=3 投票 + fallback 正则 + cli ingest 切 adapter → general_test
- [ ] Node LIF-Schema: schema +8 列 + _ensure_schema 迁移 + store 适配 → general_test
- [ ] Node LIF-Scorer: compute_lif 五维 + SOURCE_WEIGHT/LIF_WEIGHTS 常量 → general_test(dep LIF-Schema)
- [ ] Node LIF-Wire: recall boost(refresh_lif_on_recall) + consolidate 重构(_decay_one 调 compute_lif) → general_test(dep LIF-Scorer)
- [ ] Node ScoreTune: scoring weights grid + eval_recall 扩 + baseline 对比 → general_test(dep LIF-Scorer)
- [ ] P3 Regression: v2 e2e + eval_recall baseline 不退化 + ADR-5b/8v2/4v2 upheld

## 8. Open Issues
(空 — grill 已收敛 + 阶段1 范围用户确认)

## 9. Defer 预判
- PreCompact autoDream hook(阶段2)
- 向量实体tag(v4, 数据 100+)
- KG init bootstrap 全量(adapter 预留)
- autoDream daemon(PreCompact 阶段2)
- LLM cost 控制(caching/batch)
