---
name: fc-tool-call
target: http://localhost:3000/
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: NOT-WIRED
---

> NOT-WIRED: signal 全断言 SSE 事件(node_start/node_complete/tool_result/tool_use),stagehand 浏览器只看渲染文本无 SSE 可见性 → 全 false。需 qa-farm 支持 SSE 监听断言或改 curl 直测 /v1/execute SSE 流,非纯浏览器 intent。

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
