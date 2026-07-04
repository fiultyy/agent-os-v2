---
name: agent-communication
target: http://localhost:3000/memory
tags: [smoke, edge, api]
timeout_ms: 60000
---

# Agent 间通信(agent_message SSE)

> 验证 CommunicationPanel 可见 + 单 agent 无消息(空状态正常)。多 agent 编排触发 agent_message 跨页复杂(需先跑 multi-agent-orchestrate 产生消息再切通信 tab),本 intent 不覆盖,defer。

## 目标
验证 /memory 通信 tab 的 CommunicationPanel 可见,单 agent 场景下无消息(空状态正常)。

## 前置
- 系统已启动(前端 3000 + 后端 8000)

## 步骤
1. (act) 打开 /memory 页面,切到「通信」tab || button[aria-label="通信"] ::
2. (observe) 确认 CommunicationPanel 可见(面板标题 / 空状态文案)
3. (observe) 单 agent 场景下,消息列表为空(无 agent_message,正常空状态)

## 权威信号
- 通信面板(CommunicationPanel)可见
- 单 agent 线性执行时无 agent_message 事件(消息列表为空是正常)
