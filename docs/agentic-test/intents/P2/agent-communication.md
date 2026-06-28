---
name: agent-communication
target: http://localhost:3000/memory
tags: [smoke, edge, api]
timeout_ms: 120000
---

# Agent 间通信(agent_message SSE)

## 目标
验证多 agent 编排场景下 agent_message 事件流通畅,通信面板显示 agent 间消息。

## 前置
- 多 agent 场景(编排或 agent 间直接消息);单 agent 线性执行无通信事件

## 步骤
1. (act) 打开 /memory 页面,切到「通信」tab(CommunicationPanel)
2. (act) 触发多 agent 编排或 agent 间消息发送
3. (observe) 等待并查看通信面板消息流
4. (extract) 抽取 agent_message 事件中 sender_id、recipient_id、content、message_type 字段

## 权威信号
- 通信面板(CommunicationPanel)可见
- 通信面板显示消息列表,每条消息包含 sender → recipient、type、content
- agent_message 事件结构含 message_id、sender_id、recipient_id、content、message_type 字段
- message_type 取值属于 task/result/broadcast/request/response/error 之一
- 单 agent 线性执行时无 agent_message 事件(无通信面板消息为正常)
