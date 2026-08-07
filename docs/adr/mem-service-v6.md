# ADR: mem-service-v6(向量层 — embedding 召回)
Date: 2026-08-07
Status: Active(阶段 1 Accepted, 阶段 2 Proposed)
Iteration Base: 2350b0d(v5 合并后 main HEAD)
Builds on: docs/adr/mem-service-v2.md(ADR-9 baseline 盲区) + docs/adr/mem-service-v3.md(ADR-4v2 ScoreTune 盲区对权重无解)

## ADR-13: 向量层 OpenAI-compat embedding(LM Studio local-first)
Status: Accepted(阶段 1 provider); Proposed(阶段 2 recall 融合)
Context: ADR-9 baseline 实证 recall synonym/rewrite hit@5=0%(字面 m=0 盲区), ADR-4v2 ScoreTune 角点印证盲区对权重无解(syn@5/rew@5 任何单分量 weights 0%)。ScoreTune 结论: 盲区靠 vec/LLM 非权重。用户选 #2 向量层(query 侧语义召回)。embedding 选型: 用户指定本地 LM Studio REST — 实测 LM Studio server 在 port **16666**(非默认 1234; 进程在但需 ``lms load`` 模型), Ollama 在 11434(qwen3-embedding:4b 现成), CCR 3456 不支持 embedding(404)。sentence-transformers 5.4.1 已装(备选)。
Decision: OpenAI-compat ``/v1/embeddings`` provider 抽象(embedding.py), local-first: **LM Studio nomic-embed-text-v1.5**(dim 768, 区分度 syn/rew 0.44-1.0 vs irr 0.37-0.44, 轻量)默认 + **Ollama qwen3-embedding:4b**(dim 2560)fallback。passive provider(unreachable → [], 不阻断 recall)。内存 cache(MVP, 持久化 defer)。EmbeddingProvider Protocol + OpenAICompatEmbedding(base_url+model+timeout)新 provider slot in by 实现 embed。阶段 2 recall 融合(query embed → cosine vs active fact.value embed → top-N 候选扩展 + scoring δ·vec_sim 维)Proposed 待实现。
实证(阶段 1 vec baseline, eval KG 34 fact, nomic): blind(syn+rew) vec hit@5 = **14.3%**(字面 baseline 0%)— 方向证明; 英文 syn 好(rusty→rust HIT cosine 0.719), 中文 syn/rew 弱(铁锈→rust miss, nomic 中文短语 embed 近 "框架/实体" 不相关; 编程语言→开发语言 cosine 1.0 但 top 被其他中文短语污染)— nomic 中文局限, 阶段 2 实证后考虑 BGE-M3(中文强, sentence-transformers 已装)/qwen3-embedding-4b(中文原生)。
Alternatives: (a)sentence-transformers BGE-M3(已装 5.4.1, 但用户指定 LM Studio REST local-first); (b)CCR embedding(404 不支持); (c)LM Studio 默认 1234(实际 16666); (d)qwen3-embedding-4b 主用(dim 2560 重, 区分度 syn 0.575 ≈ irr 0.566 重叠, nomic 更优); (e)向量持久化 schema embedding BLOB/FAISS(MVP 内存 cache 够, 持久化 defer); (f)fact on-ingest 预计算(MVP on-recall cache, 预计算 defer)。
Consequences: query 侧语义召回(解 synonym/rewrite 字面盲区, hit@5 0%→14.3%+ 阶段 2 融合后更高); local-first(无外部 API 依赖, LM Studio+Ollama 本地); passive 不阻断 recall; 内存 cache(MVP); 中文 syn/rew nomic 局限(阶段 2 后调模型); 阶段 2 recall 融合是核心(δ·vec_sim + 候选集扩展, 需 recall.py/scoring.py 改造)。
Constrains: [T-embedding-provider(Node N 阶段 1 done), T-vec-recall(Node O 阶段 2 待)]
