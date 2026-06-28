---
id: orchestrate-no-orchestrator
title: 边界:编排缺 orchestrator 报错
page: /memory(编排 tab)
api: POST /v1/orchestrate
priority: P2
---

# 意图
验证编排的错误处理:缺 orchestrator 或参数错误时返回精确错误码(404/400/422),不崩,SSE 流不启动。

# 前置
- OrchestrationPanel 表单可提交(或直接 POST /v1/orchestrate)

# 步骤
1. 打开 /memory 编排 tab
2. 提交编排,故意用不存在的 orchestrator_agent_id(或空 sub_agents,或缺字段)

# 验证
- orchestrator_agent_id 不存在 → **404** `{"error":"Orchestrator agent not found","agent_id":"..."}`
- sub_agents 空 → **400** `{"error":"sub_agents must be non-empty"}`
- 缺必填字段(orchestrator_agent_id/sub_agents/input)→ **422** Pydantic 校验错误
- 不进入 SSE 流(校验在流之前)

# 失败模式
- orchestrator_agent_id 必填(OrchestrateRequest),缺 → 422
- sub_agents 必填非空,空 → 400
- orchestrator 不存在于 _state.agents → 404
- 校验在 SSE 流之前(orchestrate.py:89-99),错误即时返回不流式
