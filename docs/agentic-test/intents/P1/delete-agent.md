---
name: delete-agent
target: http://localhost:3000/agents
tags: [smoke, lifecycle, api]
timeout_ms: 60000
---

# 删除一个 Agent

## 目标
验证 Agent 删除通路:能从 /agents 列表删除一个 agent,被删 agent 从列表消失且后端不再返回。

## 前置
- 已创建至少一个 agent(见 create-agent)
- /agents 页面有 agent 卡片可删

## 步骤
1. (observe) 打开 /agents 页面,查看当前 agent 列表
2. (extract) 抽取当前 agent 卡片数量
3. (act) 找到要删除的 agent 卡片,hover 后点击删除按钮(垃圾桶图标)
4. (act) 在确认对话框点击「确定」
5. (observe) 查看被删 agent 是否从列表消失
6. (extract) 再次抽取 agent 卡片数量,与删除前对比应减少 1
7. (extract) 抽取 /api/agents 列表,确认不再包含被删 agent

## 权威信号
- 被删 agent 的卡片从 /agents 列表中消失
- 删除后列表 agent 数量比删除前少 1
- 删除操作未弹出「删除失败」错误提示
- GET /api/agents 响应中不再出现被删 agent 的 id
