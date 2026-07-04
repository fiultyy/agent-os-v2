---
name: agent-persist-restart
target: http://localhost:3000/agents
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: ready
---

> IT-4 决策:wait + api 模式。shell 不进 runtime(非浏览器 UI act,stagehand 在 DOM 找不到 podman restart 元素会失败)。测试者在宿主机手动执行 `podman restart agent-os-v2_orchestrator_1`;intent 用 wait 轮询 `:8001/health` 200 等服务回,再用 api GET `/api/agents` 断言持久化(自定义 agent 仍在 + 默认助手不重复创建)。

# 重启后 Agent 持久化验证

## 目标
验证重启 orchestrator 容器后自定义 agent 是否仍在(持久化双向:写 + 启动灌回),并确认默认助手不重复创建。

## 前置
- orchestrator 服务运行中(`:8001/health` 200)
- 持久化依赖 `DATABASE_URL`(pg_store);单容器无此 env 时 agent 为 in-memory,重启后丢失

## 步骤
1. (setup)创建一个自定义 agent(非默认助手),记下其 name 和 model
2. (act)测试者在宿主机手动执行 `podman restart agent-os-v2_orchestrator_1`(intent 不执行 shell,由人工触发)
3. (wait)轮询 `GET http://localhost:8001/health`,等待返回 200(orchestrator 服务已重新可用,最长 60s)
4. (api)`GET http://localhost:3000/api/agents`(或 `:8001/api/agents`),抽取 agent 列表
5. (assert)自定义 agent 仍在列表中,且 name 与 model 与重启前记录一致
6. (assert)默认助手不重复创建:重启后列表 agent 数量与重启前一致(无双倍默认项)

## 权威信号
- 重启前自定义 agent 已创建(name 与 model 已记下)
- wait 命中 `:8001/health` 200(orchestrator 服务重新可用,无健康检查失败)
- 重启后 GET `/api/agents` 中自定义 agent 完整恢复(name 与 model 与重启前一致)
- 重启后列表 agent 数量与重启前一致(默认助手不重复创建,无双倍默认项)
