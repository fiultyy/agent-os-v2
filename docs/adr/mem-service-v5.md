# ADR: mem-service-v5(KG init bootstrap — CC memory 种子)
Date: 2026-08-07
Status: Active
Iteration Base: c393cec(v4 合并后 main HEAD)
Builds on: docs/adr/mem-service-v4.md(ADR-11 upheld) + docs/adr/mem-service-v3b.md(ADR-10 autodream upheld)

## ADR-12: KG init bootstrap(CC memory → KG 种子, autodream 复用)
Status: Accepted
Context: KG 冷启动空(仅 autodream 累积 session fact, v3b/v4 本会话 transcript 整理)。CC 现有记忆(~/.claude/projects/<scope>/memory/*.md + MEMORY.md 索引)是丰富知识源(CC 机制/orchestrator/mem-service 迭代研究/本机基础设施), 未导入 KG。v4 LLM 蝴蝶翼抽取(ADR-11)已根治 regex 中文盲区(实测 LLM 4 干净 fact vs regex 2 含 yService 垃圾), 导入 CC memory 不带 regex 噪声。用户选 #3 KG init bootstrap 先于 #2 向量层(抽取质量已解, 冷启动导入不带噪声; 实证驱动顺序)。
Decision: cli `init-memory [--memory-dir <path>] [--regex]`(默认 `~/.claude/projects/-home-yy--claude/memory/`)扫 .md → autodream 管道(LLM 蝴蝶翼抽 ADR-11 + 增量决策 ADD/UPDATE/DELETE/NOOP ADR-10 + 幂等)→ KG, `fact_type=permanent`(长期知识不衰减 ADR-8), `source_ref=memory:<filename>`。autodream 加 `fact_type` 参数(默认 stable 向后兼容 v3b/v4; init-memory 传 permanent)。bootstrap.py 极薄(扫 dir + 写 tmp transcript JSONL + 调 autodream, 复用全管道 DRY; 不重写增量/抽取)。
Alternatives: (a)init-memory 独立增量(重复 autodream ADD/UPDATE/DELETE/NOOP 逻辑, 违 DRY); (b)直接 cli ingest(无增量决策, store.put_fact 不去重, 重跑重复 ADD); (c)fact_type=stable(memory 长期知识应 permanent 不衰减, ADR-8 half_life=∞); (d)跨 scope memory 联邦(仅本 scope KISS, 跨 scope defer); (e)memory 变更 mtime 增量检测(全量幂等重跑够, mtime 增量 defer)。
Consequences: KG 冷启动有 CC memory 种子(CC 机制/项目知识 fact 化); permanent 不衰减(ADR-8); 幂等(autodream 增量, 重跑 NOOP/UPDATE); 复用 autodream(consolidate + LLM 抽 + 增量, DRY); autodream 加 fact_type 参数(向后兼容默认 stable, v3b/v4 测试不破); CC memory 与 mem-service KG 叠加(不改 CC MEMORY.md, 双轨)。
Constrains: [T-init-memory, Node M]
