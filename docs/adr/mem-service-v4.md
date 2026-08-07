# ADR: mem-service-v4(蝴蝶翼 LLM 抽取接入 autodream)
Date: 2026-08-07
Status: Active
Iteration Base: a2e0567(v3b 合并后 main HEAD)
Builds on: docs/adr/mem-service-v3b.md(ADR-10 upheld) + docs/adr/mem-service-v3.md(ADR-5b adapter upheld)

## ADR-11: autodream 蝴蝶翼 LLM 抽取接入(adapter 复用, regex 降为 fallback)
Status: Accepted
Context: v3b autodream(ADR-10)用 regex extractor(ADR-5), 中文对话盲区实锤 — 本会话 6MB transcript autodream 抽 15 fact 多为垃圾片段("这次"/"的向量数据处理"/"召回率够用但不"), 英文 entity OK(MemoryService/LanceDB/RAG)。adapter(ADR-5b, v3 阶段1 A 节点)已完整实现(N=3 蝴蝶翼 fan-out 3 prompt transform + 投票 quorum ⌈n/2⌉ + confidence max 聚合 + regex fallback + merge voted 低置信度不丢), cli ingest 已用(line 55 `adapter.extract_facts`), 但 autodream.py:141 仍 `extractor.extract` 未接入(蝴蝶翼 LLM defer)。实测 adapter LLM 抽取质量远超 regex: "用户使用 rust 作为开发语言"→`用户|uses|rust`(3 翼全同意); "PreCompact hook 在 CC compact 前触发"→3 干净 fact(`PreCompact hook|connected_to|CC compact` 等, regex 完全抽不出); 无共识时 confidence 0.0 fallback(蝴蝶翼保守)。
Decision: autodream 切换 `adapter.extract_facts(text, providers=default_providers())`(N=3 蝴蝶翼 + 投票 + regex fallback), FactOut dataclass shape 适配(属性访问)。cli autodream 默认 LLM, 加 `--regex` flag 强制 regex(调试/fallback)。CCR 不可达 → adapter 自动 fallback regex(ADR-5 upheld, 非 supersede)。成本 N=3 LLM 调用/compact(compact 不频繁, 上下文满才触发, 可接受)。蝴蝶翼投票 quorum ⌈n/2⌉ 防 LLM 幻觉。
Alternatives: (a)保持 regex(defer LLM, 中文盲区未解, 实测垃圾片段); (b)autodream 独立 LLM 调用不复用 adapter(重复实现, 违 DRY, 违 ADR-5b 复用原则); (c)默认 regex + opt-in LLM(用户已选蝴蝶翼 LLM 为下一迭代最高优先, 默认 LLM); (d)每 turn LLM 抽取(成本高, PreCompact/compact 触发已够, 用户方向否定每 turn 注入)。
Consequences: autodream 抽取质量显著提升(LLM 蝴蝶翼干净三元组 vs regex 片段垃圾); CCR 不可达 fallback regex 零阻断(ADR-5 upheld); 成本 N=3 LLM/compact(可接受); FactOut shape 适配; cli ingest 与 autodream 统一 LLM 路径(此前 autodream 落后 ingest); ADR-5 regex 降为 fallback 非 supersede; fact.extractor="llm"/"regex" 标注路径(cli ingest 已有, autodream 沿用)。
Constrains: [T-llm-autodream, Node L]
