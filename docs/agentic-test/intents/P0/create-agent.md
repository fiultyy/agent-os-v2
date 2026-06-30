---
name: create-agent
target: http://localhost:3000/agents
tags: [smoke, core, canvas]
timeout_ms: 60000
status: NOT-WIRED
---

# 创建 Agent 助手

> NOT-WIRED: UI 创建表单未实现(/agents 仅一键按钮 handleCreate,name=Agent N+1/model=glm-4-flash);API CreateAgentRequest 支持 name/model/system_prompt 但 UI 未暴露

## 目标
验证用户能在 /agents 页面通过创建表单新建一个 Agent(配置名称/模型/系统提示词),创建后该 Agent 出现在列表中,系统入口操作可用。

## 前置
- 系统已启动(前端 3000 + 后端 8000)
- 已登录(若鉴权开启,需进入 /login 完成)

## 步骤
1. (act) 打开 /agents 页面
2. (act) 点击「新建 Agent」按钮(前端用默认值 POST /v1/agents:name="Agent N+1"、model="glm-4-flash")
3. (observe) 等待 Agent 列表刷新

## 权威信号
- /agents 列表出现名称为 "Agent N+1" 的 Agent 卡片
- 该 Agent 卡片显示模型为 `glm-4-flash`
- 提交后页面无错误提示(名称允许重复)
