---
name: single-agent-chat
target: http://localhost:3000/
tags: [smoke, core]
timeout_ms: 120000
---

# 与 Agent 单轮对话(SSE 流式)

## 目标
验证用户能与已存在的 agent 对话:输入问题,获得 LLM 流式回答(SSE),并完成单轮北极星交互。

## 前置
- 已创建至少一个 agent

## 步骤
1. (act) 打开对话根路由首页
2. (act) 在 agent 选择器中选择一个 agent
3. (observe) 确认消息输入框可用且发送按钮处于可点击状态
4. (act) 在输入框中填写"用一句话介绍量子计算"
5. (act) 点击发送
6. (observe) 等待对话区开始出现 agent 的流式回答文本(增量增长)
7. (extract) 抽取对话区中 agent 回答的最终完整输出文本,确认非空

## 权威信号
- agent 选择器已选中至少一个 agent
- 发送按钮在未选 agent 时禁用或无响应,选中后变为可点击
- 提交后对话区出现 agent 的回答消息区块
- 回答文本随 SSE 增量流入并最终完整可见(非空)
- 若回答中触发工具调用:对话区出现 tool_use → tool_result → 综合输出的完整序列(原生 function-calling,非正则解析)
