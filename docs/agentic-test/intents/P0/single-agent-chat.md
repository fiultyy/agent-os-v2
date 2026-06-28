---
id: single-agent-chat
title: 与 agent 单轮对话(SSE 流式)
page: /
api: POST /v1/execute
priority: P0
---

# 意图
用户想与一个已存在的 agent 对话:输入问题,获得 LLM 流式回答(SSE)。这是 MVP 北极星的核心交互(单 agent 对话)。

# 前置
- 已创建至少一个 agent(见 create-agent)

# 步骤
1. 打开 / 页面(对话根路由)
2. 在 agent 选择器选一个 agent
3. 在输入框输入"用一句话介绍量子计算"
4. 发送
5. 等待 SSE 流式回答

# 验证
- UI:对话区出现 agent 的流式回答(node_start → node_complete → 最终输出)
- API:`POST /api/execute` 返回 SSE 流,含最终 `llm_synthesize` 输出(非空)
- 若 LLM 调工具:function-calling tool_use → tool_result → 综合(原生 FC,非正则)

# 失败模式
- 未选 agent → 发送禁用/无响应
- LLM 通道错误(余额/429)→ SSE error 事件
- 单容器:经 /api/execute rewrite → 8000/v1/execute
