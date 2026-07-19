# Spec: native 主路径 v2 特性接线
Date: 2026-07-19
Status: Draft
Iteration base: a8ab786
关联 ADR: [native-v2-wiring.md](../adr/native-v2-wiring.md)

## 1. Background
pydantic-ai 移植(ADR pydantic-ai-v2-adoption)后,native /h/agent-os-v2 主路径端到端通(Observe/ToolBridge/MemoryWriter/CallToolsNode/续聊),但 3 个 v2 特性未接线/未验证:Profile(profile_registry NOT-WIRED)、R2 cache(实测未部署)、defer loading(glm 触发未 e2e)。

## 2. Goals In-Scope
- ① Profile 分层 Capability 化:engine 装 ProfileRegistry + load AGENTS.md + 每层独立 capability 注入 routes caps(ADR-1)
- ② R2 cache 接线:routes.py:347 传 model_settings(照搬 check_r2_cache)(ADR-2)
- ③ defer loading e2e:trigger + observe tool_call 验证 glm load_capability(ADR-3)

## 3. Out-of-Scope(明确 defer)
- 编译时 memory recall 注入(老 ContextCompiler Layer 2 select→user tail 自动注入)继续 defer
- 单 ProfileCapability 整体 compile(选分层 capa 化替代)
- wrap_model_request 精确 CachePoint(YAGNI,原生字段够)
- flow.py 迁 pydantic-graph(hard-block)
- P7 SubAgents(YAGNI)

## 4. User Stories
- 作 native agent 用户,trigger 时 system prompt 含 AGENTS.md persona/rules(而非裸 model)
- 作系统,native turn 智谱 /api/anthropic cache 命中(省 token)
- 作开发者,知道 glm 是否真触发 defer loading(决定 recall/skill 实际可用性)

## 5. Constraints [ADR]
- [ADR-1] Profile 分层 Capability 化 → T1
- [ADR-2] R2 cache_control 接线 → T2
- [ADR-3] defer loading e2e 验证策略 → T3

## 6. Acceptance(节点映射)
- A.T1 Profile capa:make_profile_capabilities L0→L4 + routes 注入 + 881 绿
- A.T2 cache:routes model_settings 透传 + 881 绿
- B.T3 defer e2e:observe tool_call 证据 + 结论记 memory
- green_definition:all pass + 881 回归 + native trigger 端到端 + ADR 对照

## 7. Open Issues
(无 — MUST-ASK 已对齐:Profile 分层 capa + defer trigger 验证)

## 8. Defer 预判
- 编译时 memory recall 注入(显式 defer)
- per-layer defer_loading(当前 False 常驻,未来按需)
- wrap_model_request 精确 CachePoint(若原生 cache 粒度不够)
