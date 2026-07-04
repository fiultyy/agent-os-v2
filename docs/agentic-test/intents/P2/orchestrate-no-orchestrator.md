---
name: orchestrate-no-orchestrator
target: http://localhost:8001
tags: [smoke, edge, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电(v0.5 api step):纯 API 契约测试,直 POST orchestrator :8001/v1/orchestrate 断言错误码。
> runtime **懒起 runner**(不起 Chrome);默认不带认证(SERVICE_AUTH_KEY 未配 → 放行)。
> 实测边界:缺 orchestrator_agent_id(存在但无对应 agent)→ 404 + `{"error":"Orchestrator agent not found","agent_id":"..."}`;
> 缺必填字段(空 body)→ 422 + Pydantic detail(loc/msg/type)。

# 编排缺 orchestrator 报错(边界)

## 目标
验证编排的错误处理:缺 orchestrator 或参数错误时返回精确错误码(404/422),不崩,SSE 流不启动。

## 前置
- orchestrator :8001 健康

## 步骤
1. (api) POST /v1/orchestrate body: {"orchestrator_agent_id":"no-such-id","sub_agents":[{"role":"x"}],"input":"测试"} ||| 缺 orchestrator 报 404
2. (api) POST /v1/orchestrate body: {} ||| 缺必填字段报 422(Pydantic 校验)
3. (api) POST /v1/orchestrate body: {"orchestrator_agent_id":"no-such","sub_agents":[],"input":"x"} ||| 空 sub_agents 报错

## 权威信号
- orchestrator_agent_id 不存在时,响应为 404,错误体含 "Orchestrator agent not found" 及对应 agent_id
- 缺必填字段(orchestrator_agent_id / sub_agents / input)时,响应为 422,返回 Pydantic 校验错误(detail 数组含 loc/msg/type)
- 错误即时返回(JSON 错误体,Content-Type 为 application/json,非 text/event-stream)
- 服务不崩(三个请求都返回 HTTP 响应,非连接拒绝 / 5xx)
