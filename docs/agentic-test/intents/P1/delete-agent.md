---
id: delete-agent
title: 删除一个 agent
page: /agents
api: DELETE /v1/agents/:id
priority: P1
---

# 意图
用户想删除一个不再需要的 agent,清理 agent 列表。这是 agent 生命周期的删除侧(与 create-agent 对称),验证 DELETE 通路。

# 前置
- 已创建至少一个 agent(见 create-agent)
- /agents 页面有 agent 卡片可删

# 步骤
1. 打开 /agents 页面
2. 找到要删除的 agent 卡片
3. hover 卡片,点击删除图标(Trash2 垃圾桶)
4. 在浏览器确认对话框点「确定」

# 验证
- UI:被删 agent 从列表消失,卡片数量减少
- API:`GET /api/agents` 不再返回该 agent
- 负向:再次删除同一 id → 404 `{"error":"Agent not found"}`

# 失败模式
- agent 不存在 → 404(DELETE 端点返回 `{"error":"Agent not found"}`)
- 删除不可恢复(无软删除,硬删 _state.agents)
- 前端删除失败显示「删除失败」提示
