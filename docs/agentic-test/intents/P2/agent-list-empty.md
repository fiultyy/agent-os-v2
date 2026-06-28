---
id: agent-list-empty
title: 边界:无 agent 时列表空
page: /agents
api: GET /v1/agents
priority: P2
---

# 意图
验证边界:无 agent 时 /agents 返回空列表(或确认默认 agent 总存在)。这是边界/健壮性测试,确认空列表不崩。

# 前置
- **难触发**:init_default_agent(engine startup hook)总创建「默认助手」,正常情况列表非空

# 步骤
1. (需特殊条件)清空所有 agent(删默认 + 跳过 init_default_agent)
2. `GET /v1/agents`

# 验证
- 正常:`/v1/agents` 至少返回 1 个(默认助手)
- 边界(特殊条件):空列表 `[]`
- 触发空的条件:pg 空 且 跳过 init_default_agent(生产几乎不可能);或所有 agent 被 delete 后无重建

# 失败模式
- init_default_agent 总创建默认 → 空列表难触发(默认 agent 兜底)
- 无 pg 时 in-memory,重启后默认 agent restore
- 此 intent 主要验证边界健壮性:空列表时前端不崩(/agents 页面显示空状态)
