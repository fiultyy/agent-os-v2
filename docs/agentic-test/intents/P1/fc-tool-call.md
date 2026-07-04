---
name: fc-tool-call
target: http://localhost:3000/
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: NOT-WIRED
---

> NOT-WIRED(IT-4 sse gap):collectSse Phase 0 只 CDP `Network.eventSourceMessageReceived`(只捕 EventSource API),
> 但前端 `executeWithSSE`(api.ts:171)用 **fetch + ReadableStream reader** 消费 SSE(非 EventSource)→ sse step 捕不到 → 超时无事件 fail。
> 需 qa-farm 实现 collectSse 注释提到的 fallback(page.evaluate 注入 EventSource wrapper hook)或改用
> `Network.responseReceived` + StreamResource 读 fetch streaming body。实测确认(playwright-runner/index.ts collectSse 只 session.on eventSourceMessageReceived)。
> 额外 gap:default agent glm-4-flash 可能不支持原生 tool_use(function-calling),需配 glm-4.7 agent。
> 短期替代:api step POST /v1/execute 读 SSE body 全文(fetch resp.text 等流结束),但 agent_id 动态(api step 无变量传递)+ 等流结束卡 timeout;此路不通,等 sse fallback。

# 对话中 LLM 调工具(function-calling 单轮)

## 目标
验证 LLM 面对需要工具的问题时,通过原生 function-calling(tool_use)调用工具、工具结果回注后综合回答的完整通路。

## 前置
- 已存在至少一个 agent
- 后端已注册工具(file_read/http_get/db_query 等 primitive + code_read/search 等 skill)
- LLM 支持 function-calling(anthropic 原生 tool_use)

## 步骤
1. (act) 打开应用首页,选一个 agent 进入对话
2. (act) 在输入框填写一个需要工具的问题(如「读取 /tmp/test.txt 的内容」或「搜索代码中的 foo」)
3. (act) 发送消息
4. (observe) 查看 SSE 流式响应的逐步输出
5. (extract) 抽取对话中出现的事件序列与工具调用、工具结果、最终回答文本

## 权威信号
- SSE 事件序列出现 `node_start`(llm)与对应的 `node_complete`
- 事件中出现工具调用节点(`node_start`/`node_complete` 关联 tool)
- 工具调用后出现工具结果(tool_result)文本
- 最终回答基于工具结果内容(回答引用了被读取/被搜索的真实数据,而非泛泛回应)
- 工具调用表现为原生 tool_use(非正则解析的伪调用)
- 若工具不存在或执行失败,响应中出现可观测的错误提示(如「Tool not found」或「[Tool error] ...」)
