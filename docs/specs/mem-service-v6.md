# Spec: mem-service-v6(向量层 — embedding 召回, ADR-13)
Date: 2026-08-07
Status: Draft(阶段 1 provider done, 阶段 2 recall 融合待实现)
ADR: docs/adr/mem-service-v6.md
Iteration Base: 2350b0d(v5 合并后 main HEAD)
Builds on: docs/specs/mem-service-v2.md(ADR-9 baseline 盲区实证) + docs/specs/mem-service-v3.md(ADR-4v2 ScoreTune 盲区对权重无解)
QA Available: false

## 1. Problem Statement
ADR-9 baseline 实证: recall synonym/rewrite hit@5=**0%**(字面 match m=0 盲区), 调参增益 0(ADR-4v2 角点 syn@5/rew@5 任何单分量 0%)。ScoreTune 结论: 盲区靠 vec/LLM 非权重。query 侧 synonym/rewrite(铁锈↔rust, 编程语言↔开发语言)字面无锚, 需语义召回。用户选 #2 向量层(query 侧)。

## 2. Solution
### 阶段 1(done, 本提交): embedding provider
- **embedding.py**: OpenAI-compat `/v1/embeddings` provider(LM Studio nomic-embed-text-v1.5 dim 768 默认 + Ollama qwen3-embedding:4b dim 2560 fallback), `embed(text)` + 内存 cache + passive(unreachable → [])
- **实证**(vec baseline, eval KG 34 fact): blind(syn+rew) vec hit@5 = **14.3%**(nomic, 字面 baseline 0%)— 方向证明。英文 syn 好(rusty→rust HIT), 中文 syn/rew 弱(铁锈→rust miss, nomic 中文短语局限)

### 阶段 2(待实现, 新会话): recall 向量召回融合
- **recall.py 加向量召回**: query embed → cosine vs active fact.value embed(内存 cache) → top-N 候选(扩展当前 match 候选集)
- **scoring.py 加 δ·vec_sim 维**: score = α·match+β·centrality+γ·LIF+δ·vec_sim(δ 默认 0.3, ScoreTune 调参)
- **cli recall --vector flag**(启用向量召回, 默认 off 不破现有)
- **eval_recall 加向量模式**: synonym/rewrite hit@5 字面 0% → 向量(目标 >20%)

## 3. Out-of-Scope
- 向量持久化(schema embedding BLOB / FAISS / SQLite-vec)— MVP 内存 cache, 持久化 defer
- 中文 embedding 模型调优(BGE-M3 / qwen3-embedding-4b 中文原生)— 阶段 2 实证后, nomic 中文弱时切
- fact embedding on-ingest 预计算— MVP on-recall cache, 预计算 defer

## 4. User Stories / Scenarios
1. GIVEN query "铁锈"(synonym) WHEN recall --vector THEN 候选含 "rust" fact(cosine > 字面 0)
2. GIVEN query "编程语言"(rewrite) WHEN recall --vector THEN 候选含 "开发语言"(cosine 1.0 nomic)
3. GIVEN LM Studio 不可达 WHEN embed THEN fallback Ollama; 全不可达 → [](recall 回退字面/centrality/LIF)

## 5. Implementation Decisions
- OpenAI-compat `/v1/embeddings` seam(LM Studio + Ollama 同 seam, 新 provider slot in) [ADR-13]
- LM Studio nomic 默认(用户指定 + 区分度 syn/rew 0.44-1.0 vs irr 0.37-0.44 + dim 768 轻量) + Ollama qwen3 fallback [ADR-13]
- 内存 cache(MVP, 持久化 defer) [ADR-13]
- passive provider([] fallback, 不阻断 recall) [ADR-13]
- cli recall --vector flag(默认 off, 不破现有 ADR-4v2 字面 recall) [ADR-13 阶段 2]

## 6. Testing
- 阶段 1: embedding.py `_demo`(embed dim 768) + OpenAICompatEmbedding passive(不可达 → [])+ vec baseline 实证(blind hit@5 14.3%)
- 阶段 2(待): recall --vector 向量召回 + score δ·vec_sim 融合 + eval_recall 向量 vs 字面对比

## 7. Acceptance
- [x] Node N(阶段 1): embedding.py provider + self-check + vec baseline 实证 → done
- [ ] Node O(阶段 2): recall 向量召回融合 + cli --vector + eval 向量模式 → 待(新会话, context 充裕设计 recall.py/scoring.py 改造)

## 8. Open Issues
- nomic 中文 syn/rew 弱(铁锈→rust miss, 中文短语 embed 近不相关)— 阶段 2 实证后考虑 BGE-M3(sentence-transformers 5.4.1 已装)/qwen3-embedding-4b(中文原生)
- 向量持久化 defer(MVP 内存 cache, 重启重算)

## 9. Defer 预判
- 向量持久化(schema embedding BLOB / FAISS / SQLite-vec)
- 中文 embedding 模型调优(BGE-M3 / qwen3-4b, 实证驱动)
- fact on-ingest 预计算(避免 on-recall embed 延迟)
- 跨 scope 向量联邦
