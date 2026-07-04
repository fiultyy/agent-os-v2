---
name: agent-persist-restart
target: http://localhost:8001
tags: [smoke, lifecycle, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电(api step)+ 应用限制:agent store in-memory baseline(engine.py:393),compose 无 DATABASE_URL
> → restart 丢自定义 agent,启动 init_default_agent 灌回默认助手。PostgresStore 持久化需 DATABASE_URL(deferred)。
> intent 验证 in-memory baseline restart 行为:服务回 + default 灌回(不验证自定义持久化,因 in-memory 必丢)。
> 测试者前置:podman restart orchestrator(intent 不执行 shell)。

# 重启后服务恢复 + default 灌回

## 目标
验证 orchestrator restart 后服务重新可用(health 200)+ init_default_agent 灌回默认助手(in-memory baseline 行为)。

## 前置
- 测试者手动执行 `podman restart agent-os-v2_orchestrator_1`(intent 不执行 shell)
- 等待 ~10-15s 服务重启

## 步骤
1. (api) GET http://localhost:8001/health ||| orchestrator health 200(restart 后服务回)
2. (api) GET http://localhost:8001/v1/agents ||| agent 列表(default 灌回)

## 权威信号
- [step 1] GET /health 返回 200(orchestrator restart 后服务重新可用,非 503/超时)
- [step 2] GET /v1/agents 返回 200 + JSON 数组(含 init_default_agent 灌回的"默认助手",不崩)

## 注(deferred)
> 自定义 agent 持久化:agent store in-memory(compose 无 DATABASE_URL)→ restart 丢自定义 agent,只 default 灌回。
> 持久化需 PostgresStore(DATABASE_URL 配置)+ restore_agents_from_pg(engine.py:409)。compose 默认 in-memory 是配置选择(非 bug)。
> 验证自定义持久化需配 DATABASE_URL + PostgresStore,deferred 到容器化持久化配置。
