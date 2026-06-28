---
id: fc-tool-call
title: 对话中 LLM 调工具(function-calling 单轮)
page: /
api: POST /v1/execute
priority: P1
---

# 意图
用户问一个需要工具的问题(如「读取文件 X」),LLM 通过原生 function-calling(tool_use)调工具,工具结果回注后综合回答。验证 FC 原生 tool_use 通路(defer 通电项,anthropic 原生 tool_use + openai 兼容)。

# 前置
- 已创建一个 agent
- 后端注册工具(15 primitive:file_read/http_get/db_query 等 + 3 skill:code_read/search 等)
- LLM 支持 function-calling(anthropic 原生 tool_use)

# 步骤
1. 打开 / 页面,选一个 agent
2. 输入需要工具的问题(如「读取 /tmp/test.txt 的内容」或「搜索代码中的 foo」)
3. 发送,观察 SSE 流

# 验证
- SSE 事件序列:`node_start(llm)` → `node_complete(llm, 决定调工具)` → `node_start(tool)` → `node_complete(tool, 工具结果)` → 综合
- 最终回答基于工具结果(LLM 拿到 tool_result 后回答)
- 原生 tool_use(非正则解析),tool_result 回注下一轮

# 失败模式
- 工具不存在 → tool_result 含「Tool not found」+ PitFail 记录(/v1/pitfall 可查)
- 工具执行失败(超时/文件不存在/权限)→ tool_result「[Tool error] ...」+ PitFail(error 分类)
- LLM 不支持 FC → 无 tool_use,直接回答(降级,正常)
