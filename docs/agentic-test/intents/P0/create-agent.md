---
id: create-agent
title: 创建一个助手 agent
page: /agents
api: POST /v1/agents
priority: P0
---

# 意图
用户想创建一个新的 agent 助手,配置名称/模型/系统提示词,以便后续与之对话或用于多 agent 编排。这是系统的入口操作(无 agent 无法对话/编排)。

# 前置
- 系统 running(前端 3000 + 后端 8000)
- 已登录(若鉴权开启,/login)

# 步骤(自然语言,agentic 自主执行)
1. 打开 /agents 页面
2. 在创建表单填写:名称"研究助手"、model 选 `glm-4.7`、system_prompt"你是研究助手,善于用中文简洁回答"
3. 点击「创建」
4. 等待列表刷新

# 验证(可观测判据)
- UI:/agents 列表出现"研究助手"卡片
- API:`GET /api/agents` 返回含 `name="研究助手"` `model="glm-4.7"` 的项

# 失败模式(已知边界)
- model 必填(空 → 创建失败);名称允许重复(无唯一约束)
- 单容器部署:前端经 `/api/*` rewrite → `8000/v1/agents`(next.config 已加 /v1,6250c82)
