# ADR: native 主路径 v2 特性接线
Date: 2026-07-19
Status: Active
Iteration base: a8ab786
关联: [pydantic-ai-v2-adoption.md](pydantic-ai-v2-adoption.md)(P8 + defer 收尾)

## ADR-1: Profile 分层 Capability 化
Status: Accepted
Context: ProfileCapability 已写(pydantic-ai-v2-adoption P2 done)但 `profile_registry` NOT-WIRED(该 ADR line 38/72 明确"移植时接线决策,避免又造死代码")+ SOUL.md 不存在(只有 AGENTS.md)→ SOUL/AGENTS persona/rules 不进 native turn。用户要求分层 CA 拆 capa 化(不只单 ProfileCapability)。
Decision:
- `engine.py` 装 `ProfileRegistry`(`_state.profile_registry = ProfileRegistry()`)+ `load_from_files(agent_id="native", workspace=项目根)`,加载 AGENTS.md(L1 identity + L2 guidelines;L0 SOUL.md 缺静默跳过)
- **分层 capa 化**:每 `LayerProfile` → 独立 capability 实例(新 `LayerCapability` dataclass:layer/source/content,或 ProfileCapability 单层实例),`get_instructions` 返该层 content(带 `=== source (Lx) ===` 标记,保持 compile 格式)
- `make_profile_capabilities(profile) -> list[Capability]`,按 L0→L4 序生成
- `routes.py _build_native_session` caps `extend(profile_caps)`,pydantic-ai 按 caps 序拼 system prompt → L0→L4 叠加
- `defer_loading=False`(常驻,保 L0-L5 确定性叠加;pydantic-ai-v2-adoption 不推荐按层 defer)
Alternatives: 单 ProfileCapability 整体 compile() — 否决,不够"分层 capa 化";routes.py:347 传 `instructions=compile()` — 否决,不经 capability 失去 per-layer 管理
Consequences: profile 每层独立 capability(可独立 order/未来 per-layer defer);native turn system prompt 含 AGENTS.md persona/rules(L1+L2)
Constrains: [T1]

## ADR-2: R2 cache_control 接线
Status: Accepted
Context: check_r2_cache.py 实测 98% 命中(dict 字段已验证),但 routes.py:347 `build_native_agent(capabilities=caps)` 未传 model_settings → native 实际无 cache(pydantic-ai-v2-adoption line 145 实测但部署未接)
Decision: routes.py:347 改 `build_native_agent(capabilities=caps, model_settings={"anthropic_cache_instructions":"5m","anthropic_cache_tool_definitions":"5m"})`,照搬 check_r2_cache 已验证字段
Alternatives: `wrap_model_request` capability 插 CachePoint(精确控制) — 否决,YAGNI(check_r2_cache 已证原生字段够)
Consequences: native turn 智谱 /api/anthropic cache 命中(instructions + tool defs 5m TTL)
Constrains: [T2]

## ADR-3: defer loading e2e 验证策略
Status: Accepted
Context: MemoryCapability/SkillCapability defer_loading=True,glm 是否真 `load_capability('memory'/'skill_xxx')` 触发 recall/skill 注入未 e2e(pydantic-ai-v2-adoption 待验证)
Decision: trigger + observe tool_call 验证 — 造场景(memory/skill 相关 query 如 "recall workspace experience" 或显式要求用某 skill),curl observe `/sessions/agent-os-v2/<sid>/events` 看是否出现 `experience_memory`/`kg_memory`/`skill_xxx` 的 tool_call event(证明 glm load_capability 触发)。记录结论(pass/fail + observe 证据)。glm 不触发 load 记为 finding(非阻塞,后续 ADR)
Alternatives: 单测 mock load_capability — 否决,只验机制不验 glm 真触发
Consequences: defer loading 端到端验证(glm 真触发 vs 静默不 load);结论沉淀 memory
Constrains: [T3]
