# Spec: mem-service-v4(蝴蝶翼 LLM 抽取接入 autodream)
Date: 2026-08-07
Status: Draft
ADR: docs/adr/mem-service-v4.md
Iteration Base: a2e0567(v3b 合并后 main HEAD)
Builds on: docs/specs/mem-service-v3b.md(adapter/llm_provider v3 阶段1 已完整实现)
QA Available: false

## 1. Problem Statement
v3b autodream(ADR-10)用 regex extractor(ADR-5), 中文对话盲区实锤: 本会话 6MB transcript autodream 抽 15 fact 多为垃圾片段("这次"/"的向量数据处理"/"召回率够用但不"), 英文 OK。adapter(ADR-5b, v3 阶段1)已完整实现(N=3 蝴蝶翼 fan-out + 投票 quorum ⌈n/2⌉ + confidence max + regex fallback + merge voted), cli ingest 已用(line 55), 但 autodream.py:141 仍 `extractor.extract` 未接入。实测 adapter LLM 抽取质量远超 regex("用户使用 rust"→用户|uses|rust 干净; "PreCompact hook..."→3 干净 fact, regex 抽不出)。

## 2. Solution (In-Scope)
- **autodream.py 切换** `extractor.extract(text)`(line 141)→ `adapter.extract_facts(text, providers=...)` + FactOut dataclass shape 适配(属性访问 subject/predicate/object 替代当前 dict.get)
- **providers 注入**: autodream 接 providers 参数(默认 `adapter.default_providers()`; `providers=[]` 强制 regex fallback, ADR-5 upheld)
- **cli autodream `--regex` flag**(默认 LLM; `--regex` 强制 regex 调试/fallback)
- **cli autodream wrapper docstring 更新**(defer → 接入 ADR-11)
- **tests/test_autodream.py**: LLM 路径 mock provider(返固定 Extraction 验 ADD/UPDATE/DELETE/NOOP 四路径, 复用 fixture, 避真实 CCR 依赖) + 现有 regex 测试(providers=[] fallback)保留
- **实测对比**(P3): 同 transcript LLM autodream vs regex autodream, fact 质量(干净 subject/predicate/object 比例)— v4 核心价值证明

## 3. Out-of-Scope
- 新 provider(claude-api/LMstudio stub 保留, ADR-5b defer 到 deploy target)
- 向量层(query 侧 synonym/rewrite, v5)
- KG init bootstrap(冷启动, v6)
- adapter 本身改造(已完整, 只接入)
- autoDream daemon(CC flag 未开)

## 4. User Stories / Scenarios
1. GIVEN transcript 含中文对话 WHEN `autodream`(LLM 默认) THEN KG 抽干净 fact(subject/predicate/object, 非片段垃圾)
2. GIVEN CCR 不可达 WHEN `autodream` THEN adapter fallback regex(ADR-5 upheld, 零阻断)
3. GIVEN `cli autodream --regex` THEN 强制 regex 路径(调试/fallback 验证)
4. GIVEN 同 transcript: LLM autodream 抽 fact 质量评分 > regex autodream(实测对比, fact 干净度)

## 5. Implementation Decisions
- autodream default LLM(`adapter.extract_facts` + `default_providers=[CCRProvider]`), fallback regex(ADR-5) [ADR-11]
- cli autodream `--regex` flag(强制 regex 路径) [ADR-11]
- FactOut dataclass shape 适配(属性访问) [ADR-11]
- 蝴蝶翼投票(quorum ⌈n/2⌉)防 LLM 幻觉, 低置信度 merge voted 不丢 [ADR-5b upheld]

## 6. Testing Decisions
- Seams: autodream(mock provider 注入) + cli autodream `--regex`
- LLM 路径: mock provider 返固定 Extraction, 验 autodream 四路径(ADD/UPDATE/DELETE/NOOP)复用 fixture
- fallback 路径: `providers=[]` → regex, 现有 regex 测试保留
- 实测: 同 transcript LLM vs regex, fact 质量(干净三元组比例 + 抽取数对比)

## 7. Acceptance(关联编排图节点)
- [ ] Node L: autodream swap adapter + FactOut shape + cli `--regex` + mock 测试 → general_test
- [ ] P3 Regression: v3b e2e 不破 + LLM vs regex 实测质量对比 + ADR-11 upheld

## 8. Open Issues
(空 — adapter 已实现 v3 阶段1, v4 只接入)

## 9. Defer 预判
- 新 provider(claude-api/LMstudio, deploy target 驱动)
- 向量层 v5(query 侧 synonym/rewrite, 数据 100+ baseline)
- KG init bootstrap v6(抽取质量解后冷启动导入)
- autoDream daemon(CC server-side flag)
