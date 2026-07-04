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
2. (observe) 页面挂载自动选中第一个 agent(页眉显示已选 agent 名)
3. (observe) 确认消息输入框可用且发送按钮处于可点击状态
4. (act) 在输入框中填写"用一句话介绍量子计算"
5. (act) 点击发送
6. (wait) 等待 execution_complete 事件(SSE 综合输出完成,约 5-30s)
7. (extract) 抽取对话区中 agent 回答的最终完整输出文本,确认非空

## 权威信号
- [step 2] 页眉显示已选 agent 名(自动选中第一个 agent)
- [step 3] 消息输入框 + 发送按钮存在可交互(发送按钮渲染,不验 disabled 状态)
- [step 7] 对话区出现 agent 的最终回答(extract 抽取非空)
- [step 7] 回答文本随 SSE 增量流入并最终完整可见(非空)
