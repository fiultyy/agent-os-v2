# ADR: mem-service-v3b 阶段2(PreCompact autoDream hook)
Date: 2026-08-07
Status: Active
Iteration Base: 00fdf10(v3 阶段1 spec HEAD)
Builds on: docs/adr/mem-service-v3.md(ADR-5b/8v2/4v2 upheld)

## ADR-10: PreCompact autoDream hook(session raw→KG 增量整理)
Status: Accepted
Context: v3 阶段1 adapter/LLM 抽取补 ingest 侧中文盲区, LIF 五维补召回强化, 但 session raw(对话 transcript)未日常整理进 KG(autoDream defer)。CC compact 时 session 压缩丢, KG 无增量。用户方向: agent 自主召回(非 UserPromptSubmit 自动注入, 否定 memory 终设增量条); PreCompact(recompact)触发 autoDream 整理 session→KG 增量。
Decision: **PreCompact hook**(CC compact 前触发, stdin JSON {session_id, transcript_path, trigger:"manual"|"auto", cwd}) → 调 `mem autodream --session <id> --transcript <path>`(cli 新子命令) → 内部: (a)consolidate.consolidate()(decay+dedup, v2/v3 已有复用); (b)session→facts 抽取(复用 extractor.extract() regex ADR-5, 扫 transcript user/assistant message.content 拼文本喂 extract; 蝴蝶翼 LLM defer, adapter 预留复用); (c)增量决策(ADD 新 fact / UPDATE 已存 LIF/confidence / DELETE 矛盾 superseded / NOOP)。hook **exit 0 放行** compact(不阻断; 副作用落盘 KG)。**agent 自主召回**(非 hook 注入; 用户方向否定 UserPromptSubmit 增量条; PreCompact 无 additionalContext 注入能力 — 注入需 SessionStart(compact) 但用户否定自动注入, 走 /mem 按需)。
Alternatives: (a)UserPromptSubmit 自动注入(用户否定, 性能/污染风险); (b)SessionStart(compact) 注入 KG 摘要(自动注入, 用户否定 agent 自主); (c)autoDream daemon(CC server-side flag 未开, defer)。
Consequences: session raw 日常整理进 KG(compact 触发, 非每 turn); outward 改 CC settings(注册 PreCompact hook, CLAUDE.md claim 过时修); regex 复用(蝴蝶翼 defer, adapter 预留); agent 自主召回(/mem 按需); transcript 异步写入(hook 可能不含最新消息, 容忍)。
Constrains: [T-autodream, T-precompact-hook, Node J, Node K]
