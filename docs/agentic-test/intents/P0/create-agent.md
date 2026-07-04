---
name: create-agent
target: http://localhost:3000/agents
tags: [smoke, core, canvas]
timeout_ms: 60000
status: ready
---

# 创建 Agent 助手

> /agents 创建表单已实现(commit 30b0089):点击「新建 Agent」展开表单(name input + model select + system_prompt textarea),填后点「创建 agent」提交 POST /v1/agents。

## 目标
验证用户能在 /agents 页面点击「新建 Agent」展开表单,填写 name/model 后提交,创建后该 Agent 出现在列表中。

## 前置
- 系统已启动(前端 3000 + 后端 8000)
- 已登录(若鉴权开启,需进入 /login 完成)

## 步骤
1. (act) 打开 /agents 页面
2. (act) 点击「新建 Agent」按钮展开创建表单 || button[aria-label="新建 Agent"] ::
3. (act) 在 name 输入框填写 Agent 名称 || input[aria-label="agent 名称"] :: 测试助手
4. (act) 选择 model(select 下拉,默认第一项)|| select[aria-label="agent 模型"] :: glm-4-flash
5. (act) 可选:填写 system_prompt || textarea[aria-label="agent 系统提示词"] :: 你是测试助手
6. (act) 点击「创建 agent」提交按钮 || button[aria-label="创建 agent"] ::
7. (observe) 等待 Agent 列表刷新,新 Agent 出现

## 权威信号
- /agents 列表出现名称为 "测试助手" 的 Agent 卡片(用户填的 name,非默认 Agent N+1)
- 该 Agent 卡片显示模型为 `glm-4-flash`(用户选的 model)
- 提交后页面无「创建失败」错误文案(成功 Alert/toast 可接受,非失败提示;信号1 卡片出现即证明创建成功)
