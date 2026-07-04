---
name: agent-persist-restart
target: http://localhost:3000/agents
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: NOT-WIRED
---

> NOT-WIRED: intent 把"重启 orchestrator 容器(podman restart)"当 browser act,但这是宿主机 infra 命令非浏览器 UI —— stagehand 在 DOM 找不到元素必然 "No action found"。需 qa-farm 支持 infra 步骤(host shell/exec)或分离为纯 API 测试(curl 创建 + podman restart + GET 验证持久化),非纯浏览器 intent。

# 重启后 Agent 持久化验证

## 目标
验证重启 orchestrator 容器后自定义 agent 是否仍在(持久化双向:写 + 启动灌回),并确认无 DATABASE_URL 时的 in-memory 限制。

## 前置
- 已创建一个自定义 agent(非默认助手),记下其 name 和 model
- 持久化依赖 `DATABASE_URL`(pg_store);单容器无此 env 时 agent 为 in-memory,重启后丢失

## 步骤
1. (observe) 查看重启前 /agents 页面或 GET /api/agents,确认自定义 agent 存在
2. (extract) 记下重启前自定义 agent 的 name 和 model
3. (act) 重启 orchestrator 容器(如 `podman restart agent-os-orchestrator` 或重建)
4. (observe) 查看重启完成后 orchestrator 服务已重新可用(打开 /agents 页面能加载)
5. (extract) 重启后 GET /api/agents,抽取 agent 列表
6. (observe) 查看重启后列表中自定义 agent 是否仍在(name 与 model 是否匹配重启前记录)

## 权威信号
- 重启前自定义 agent 在列表中可见(其 name 与 model 已记下)
- 重启后 /agents 页面能正常加载(无服务不可用错误)
- 配置了 DATABASE_URL(pg 持久)时:重启后列表中自定义 agent 完整恢复(name 与 model 与重启前一致)
- 配置了 DATABASE_URL 时:默认助手不重复创建(重启后列表 agent 数量与重启前一致,无双倍默认项)
- 无 DATABASE_URL(单容器 in-memory)时:重启后自定义 agent 丢失,列表只剩默认助手(已知限制,非 bug)
- pg 连接失败时降级 in-memory 且服务仍能启动(列表为空或仅默认助手,无启动失败)
