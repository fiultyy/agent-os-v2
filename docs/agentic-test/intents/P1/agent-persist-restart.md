---
id: agent-persist-restart
title: 重启 orchestrator 后 agent 仍在(或确认 in-memory 限制)
page: /agents
api: GET /v1/agents
priority: P1
---

# 意图
用户重启 orchestrator 容器/进程后,期望自定义 agent 仍在(持久化双向:写 + 启动灌回)。验证 agent 持久化是否工作 —— 同时确认单容器无 DATABASE_URL 时的 in-memory 限制。

# 前置
- 已创建一个自定义 agent(非默认助手),记下 id
- 持久化依赖 `DATABASE_URL`(pg_store);单容器无此 env 时 agent 是 in-memory

# 步骤
1. 创建一个自定义 agent,记下 id 和 name
2. 重启 orchestrator(如 `podman restart agent-os-orchestrator` 或重建)
3. 重启完成后,打开 /agents 页面 或 `GET /api/agents`

# 验证
- **有 DATABASE_URL**(pg 持久):自定义 agent 完整恢复(name/model/system_prompt/tools);默认助手不重复创建(restore 后列表非空,init_default_agent 跳过)
- **无 DATABASE_URL**(单容器 in-memory):重启后自定义 agent 丢失,只剩默认助手 —— 这是已知限制(in-memory),非 bug;需配 DATABASE_URL 才持久
- API:`GET /api/agents` 重启后的列表

# 失败模式
- pg 连接失败 → 降级 in-memory(agent 丢,但不影响服务启动)
- 无 pg 时自定义 agent 重启丢失(in-memory 限制)
- restore 异常返回 0,不影响启动流程
