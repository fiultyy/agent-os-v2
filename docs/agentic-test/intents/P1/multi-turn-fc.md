---
id: multi-turn-fc
title: 多轮工具调用循环(tool→llm→synthesize)
page: /
api: POST /v1/execute
priority: P1
---

# 意图
用户问一个需要多步工具的问题(如「读 A 文件,再基于内容搜 B」),LLM 多轮调工具,每轮 tool_result 回注下一轮,最终综合。验证多轮 tool_use loop(defer 通电项:tool→llm 循环 + tool_result 回注 + MAX_TOOL_ITERATIONS)。

# 前置
- 已创建 agent + 工具注册
- LLM 支持多轮 function-calling

# 步骤
1. 打开 / 页面,选 agent
2. 输入需要多步工具的问题(如「读取 X.txt,然后搜索其中提到的关键词 Y」)
3. 发送,观察 SSE 流的多轮 tool/llm 交替

# 验证
- SSE 多轮:Round1 `node_start(llm)`→`tool` → Round2 `node_start(llm)`→`tool` → ... → `node_start(llm_synthesize)` → 最终答案
- 每轮 tool_result 回注下一轮 LLM(_inject_tool_history:assistant tool_use + user tool_result)
- 死循环保护:`MAX_TOOL_ITERATIONS=5`(env 可配),超限强制进 llm_synthesize

# 失败模式
- 死循环保护:达 MAX_TOOL_ITERATIONS=5 强制 synthesize(不无限调工具)
- 中途工具失败:tool_result(error)回注,LLM 决定继续或终止
- LLM 提前停止调工具 → 直接 synthesize(正常终止)
