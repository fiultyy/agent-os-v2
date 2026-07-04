---
name: orchestrate-no-orchestrator
target: http://localhost:3000/memory
tags: [smoke, edge, api]
timeout_ms: 60000
status: NOT-WIRED
---

> NOT-WIRED: 5 个信号全断言 HTTP 响应行为(404/400/422 状态码、错误体、不进 SSE 流),但浏览器 stagehand 只能观察 UI 外观,无网络层证据 → 全 false。纯 API 契约测试应 curl /v1/orchestrate 直测(缺 orchestrator_id → 404;sub_agents 空 → 400),非浏览器 intent。需 qa-farm API 直测模式。

# 编排缺 orchestrator 报错(边界)

## 目标
验证编排的错误处理:缺 orchestrator 或参数错误时返回精确错误码(404/400/422),不崩,SSE 流不启动。

## 前置
- OrchestrationPanel 表单可提交(或直接 POST /v1/orchestrate)

## 步骤
1. (act) 打开 /memory 编排 tab
2. (act) 提交编排,故意使用不存在的 orchestrator_agent_id(或空 sub_agents,或缺必填字段)

## 权威信号
- orchestrator_agent_id 不存在时,响应为 404,错误体含 "Orchestrator agent not found" 及对应 agent_id
- sub_agents 为空时,响应为 400,错误文案含 "sub_agents must be non-empty"
- 缺必填字段(orchestrator_agent_id / sub_agents / input)时,响应为 422,返回 Pydantic 校验错误
- 错误即时返回,不进入 SSE 流(无 text/event-stream 连接或流式输出)
- 页面未崩溃,错误提示可见
