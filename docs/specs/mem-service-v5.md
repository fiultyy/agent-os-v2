# Spec: mem-service-v5(KG init bootstrap — CC memory 种子)
Date: 2026-08-07
Status: Draft
ADR: docs/adr/mem-service-v5.md
Iteration Base: c393cec(v4 合并后 main HEAD)
Builds on: docs/specs/mem-service-v4.md(蝴蝶翼 LLM 抽取 ADR-11)
QA Available: false

## 1. Problem Statement
KG 冷启动空(仅 autodream 累积 session fact)。CC 现有记忆(~/.claude/projects/<scope>/memory/*.md, MEMORY.md 索引)是丰富知识源(CC 机制/orchestrator/mem-service 迭代研究), 未导入 KG。v4 LLM 蝴蝶翼抽取(ADR-11)已根治 regex 中文盲区, 导入 CC memory 不带 regex 噪声(实测 LLM 4 干净 fact vs regex 2 垃圾)。

## 2. Solution (In-Scope)
- **cli `init-memory [--memory-dir <path>] [--regex]`**: 扫 memory dir 的 .md(默认 `~/.claude/projects/-home-yy--claude/memory/`), 每个 .md 走 autodream 管道(LLM 抽 + 增量决策 ADD/UPDATE/DELETE/NOOP + 幂等)→ KG, `fact_type=permanent`(长期知识不衰减 ADR-8), `source_ref=memory:<filename>`
- **autodream 加 `fact_type` 参数**(默认 stable 向后兼容; init-memory 传 permanent): ADD/DELETE 路径 put_fact 用 fact_type
- **bootstrap.py** 极薄(`init_memory(memory_dir, providers, fact_type="permanent")`: 扫 .md 排序 → 读文本 → 写 tmp transcript JSONL(`type=user, content=md_text`)→ `autodream.autodream(session_id="memory:<file>", transcript_path=tmp, providers, fact_type)` → 累加 counts)
- **幂等**: 重跑 init-memory → 已存 fact NOOP/UPDATE, 不重复 ADD(autodream 增量决策)

## 3. Out-of-Scope
- 向量层(query 侧 synonym/rewrite, #2 延后)
- autoDream daemon(CC flag 未开)
- 跨 scope memory(仅本 scope -home-yy--claude, KISS)
- memory 文件变更增量检测(全量幂等重跑够)

## 4. User Stories / Scenarios
1. GIVEN CC memory dir 含 .md WHEN `cli init-memory` THEN KG ADD permanent fact(LLM 抽, source_ref=memory:<file>)
2. GIVEN 同 memory dir 重跑 `init-memory` THEN NOOP/UPDATE(幂等, 不重复 ADD)
3. GIVEN .md 含 "用户使用 rust" WHEN init-memory THEN KG permanent fact(用户,uses,rust), fact_type=permanent

## 5. Implementation Decisions
- init-memory 复用 autodream 管道(tmp JSONL + autodream) [ADR-12]
- fact_type=permanent(memory 长期知识不衰减 ADR-8) [ADR-12]
- autodream 加 fact_type 参数(默认 stable 向后兼容) [ADR-12]
- 默认 memory dir ~/.claude/projects/-home-yy--claude/memory/(本环境 CC scope)

## 6. Testing Decisions
- Seams: init-memory(mock memory dir + mock LLM/tmp provider)
- 导入: mock dir 含 .md → init-memory → KG permanent fact
- 幂等: 重跑 init-memory → added==0 noop>=1
- fact_type: 导入 fact fact_type=="permanent"
- v4 回归: autodream 测试不破(fact_type 默认 stable)

## 7. Acceptance(关联编排图节点)
- [ ] Node M: init-memory + autodream fact_type + bootstrap.py + mock 测试 → general_test
- [ ] P3 Regression: v4 e2e 不破 + init-memory 幂等 + ADR-12 upheld

## 8. Open Issues
(空 — autodream 管道已验证 v3b/v4, init-memory 复用)

## 9. Defer 预判
- 向量层 #2(query 侧, 数据 100+ baseline)
- 跨 scope memory(多 CC project 联邦)
- memory 变更增量检测(mtime + 增量 init)
