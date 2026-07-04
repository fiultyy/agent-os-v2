---
name: multi-turn-fc
target: http://localhost:3000/
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: NOT-WIRED
---

> NOT-WIRED: signal 多断言 SSE 节点(node_start(llm)/node_start(llm_synthesize)/tool_result 回注/MAX_TOOL_ITERATIONS),stagehand 看不到 SSE → 全 false。需 qa-farm SSE 监听或 curl 直测,非纯浏览器 intent。

# 多轮工具调用循环(tool→llm→synthesize)

## 目标
验证 LLM 能多轮调用工具,每轮 tool_result 回注下一轮,最终综合出答案,且受 MAX_TOOL_ITERATIONS 死循环保护。

## 前置
- 已创建 agent 并完成工具注册
- LLM 支持多轮 function-calling

## 步骤
1. (act) 在首页对话区选一个已注册工具的 agent
2. (act) 在消息输入框填写需要多步工具的问题(如「读取 X.txt,然后搜索其中提到的关键词 Y」)
3. (act) 发送消息,触发对话执行(POST /v1/execute)
4. (observe) 查看 SSE 流持续输出,可见多轮 tool 与 llm 节点交替出现
5. (extract) 抽取 SSE 流中的轮次结构:Round1 node_start(llm)→tool 出现 → Round2 node_start(llm)→tool 出现 → ... → 最终出现 node_start(llm_synthesize) 与最终答案
6. (extract) 抽取每轮的 tool_result 是否回注下一轮 LLM(assistant tool_use 后跟 user tool_result)
7. (observe) 查看最终综合答案在对话区可见
8. (observe) 当工具调用达到 MAX_TOOL_ITERATIONS 上限时,查看流程被强制进入 llm_synthesize(不再继续无限调工具)
9. (observe) 当中途工具返回错误时,查看 tool_result(error) 回注后 LLM 决定继续或终止

## 权威信号
- SSE 流中出现多轮 node_start(llm) 与 tool 交替节点
- SSE 流最终出现 node_start(llm_synthesize) 节点
- 对话区显示最终综合答案文本
- 每轮 tool_result 回注下一轮 LLM(assistant tool_use 之后跟随 user tool_result)
- 工具调用达 MAX_TOOL_ITERATIONS 上限时强制进入 llm_synthesize,不再继续调工具
- 工具失败时 tool_result(error) 回注 LLM,流程正常继续或终止
- LLM 提前停止调工具时直接进入 llm_synthesize 正常终止
