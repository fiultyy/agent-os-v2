---
name: create-agent
target: http://localhost:3000/agents
tags: [smoke, core, canvas]
timeout_ms: 60000
---

# 创建 Agent 助手

## 目标
验证用户能在 /agents 页面通过创建表单新建一个 Agent(配置名称/模型/系统提示词),创建后该 Agent 出现在列表中,系统入口操作可用。

## 前置
- 系统已启动(前端 3000 + 后端 8000)
- 已登录(若鉴权开启,需进入 /login 完成)

## 步骤
1. (act) 打开 /agents 页面
2. (observe) 查看创建表单可见
3. (act) 在创建表单中填写名称"研究助手"
4. (act) 在创建表单中为 model 选择 `glm-4.7`
5. (act) 在 system_prompt 字段填写"你是研究助手,善于用中文简洁回答"
6. (act) 点击「创建」按钮
7. (observe) 等待 Agent 列表刷新

## 权威信号
- /agents 列表中出现名称为"研究助手"的 Agent 卡片
- 该 Agent 卡片显示模型为 `glm-4.7`
- 提交后页面无错误提示(model 为空时会创建失败;名称允许重复)
