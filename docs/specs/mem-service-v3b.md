# Spec: mem-service-v3b 阶段2(PreCompact autoDream hook)
Date: 2026-08-07
Status: Draft
ADR: docs/adr/mem-service-v3b.md
Iteration Base: 00fdf10
Builds on: docs/specs/mem-service-v3.md(v3 阶段1 upheld)
QA Available: false

## 1. Problem Statement
v3 阶段1 adapter/LIF/ScoreTune green, 但 session raw(对话 transcript)未日常整理进 KG(autoDream defer)。CC compact 时 session 压缩丢, KG 无增量。用户方向: PreCompact 触发 autoDream 整理 session→KG, agent 自主召回(非自动注入)。

## 2. Solution (In-Scope)
- **autodream.py + cli autodream 子命令**(`mem autodream --session <id> --transcript <path>`): consolidate(decay+dedup 复用 v2/v3) + session→facts 抽取(regex 复用 extractor.extract, 扫 transcript user/assistant message.content) + 增量决策(ADD/UPDATE/DELETE/NOOP)
- **PreCompact hook 脚本**(~/.claude/hooks/pre-compact-mem.sh): 读 stdin JSON(transcript_path/session_id) → 调 `mem autodream` → exit 0 放行(不阻断 compact)
- **settings.json 注册 PreCompact hook**(outward 改 CC 配置)
- **agent 自主召回**(非 UserPromptSubmit 注入, /mem 按需; 用户方向)

## 3. Out-of-Scope
- 蝴蝶翼 LLM 抽取 session(defer, regex 先; adapter 预留复用)
- SessionStart(compact) 注入 KG 摘要(自动注入, 用户否定 agent 自主)
- autoDream daemon(CC server-side flag 未开)
- 向量实体tag(v4)
- query 独立 cli(recall --verbose 已替代)

## 4. User Stories / Scenarios
1. CC `/compact` → PreCompact hook 触发 → `mem autodream --transcript <path>` 整理 session→KG 增量 → exit 0 放行 compact
2. CC recall(`/mem recall`) → KG 含 compact 前整理的 session fact(agent 自主, 非自动注入)
3. GIVEN transcript 含 "用户使用 rust" WHEN autodream THEN KG ADD fact(用户,uses,rust)
4. GIVEN 同 fact 已存 WHEN autodream THEN UPDATE(NOOP/refresh LIF, 非重复 ADD)

## 5. Implementation Decisions
- PreCompact hook → mem autodream(consolidate + session→facts regex + 增量决策 ADD/UPDATE/DELETE/NOOP) [ADR-10]
- agent 自主召回(非注入, 用户方向) [ADR-10]

## 6. Testing Decisions
- Seams: cli autodream(喂 transcript fixture JSONL) + hook 脚本(读 stdin JSON mock)
- autodream: ADD/UPDATE/DELETE/NOOP 四路径 + 幂等(同 transcript 两次 autodream 第二次 NOOP)
- hook: exit 0 放行 + 调 cli autodream(transcript_path 透传)
- outward settings 注册: 加 PreCompact hook(不动其他 hook)

## 7. Acceptance(关联编排图节点)
- [ ] Node J: autodream.py + cli autodream 子命令 + session→facts regex + 增量决策(ADD/UPDATE/DELETE/NOOP) + 幂等 → general_test
- [ ] Node K: precompact hook 脚本(~/.claude/hooks/) + settings.json 注册 PreCompact + exit 0 放行 → general_test(dep J)
- [ ] P3 Regression: v3 阶段1 e2e 不破(6 passed) + autodream 增量幂等 + ADR-10 upheld

## 8. Open Issues
(空 — grill v3-precompact 已给设计 + claude-code-guide 确认 hook 机制)

## 9. Defer 预判
- 蝴蝶翼 LLM session 抽取(adapter 预留复用, regex 先)
- SessionStart(compact) 注入(用户否定 agent 自主)
- autoDream daemon(CC flag 未开)
- 向量层 v4(数据 100+)
