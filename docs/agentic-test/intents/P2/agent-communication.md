---
id: agent-communication
title: agent 间通信(agent_message SSE)
page: /memory(通信 tab)
api: SSE agent_message
priority: P2
---

# 意图
用户观察 agent 间的通信消息(agent_message 事件),验证多 agent 通信桥(CommunicationBus → emit_agent_message → SSE → CommunicationPanel)。

# 前置
- 多 agent 场景(编排 或 agent 间直接消息);单 agent 线性执行无通信事件

# 步骤
1. 打开 /memory 页面,切到「通信」tab(CommunicationPanel)
2. 触发多 agent 编排(见 multi-agent-orchestrate)或 agent 间消息
3. 观察 agent_message 事件流

# 验证
- SSE:`event: agent_message` + `{message_id, sender_id, recipient_id, content, message_type}`
- UI:CommunicationPanel 显示消息列表(sender → recipient,type,content,priority)
- message_type:task/result/broadcast/request/response/error

# 失败模式
- 单 agent 线性执行 → 无 agent_message 事件(正常)
- 桥接失败(_bridge_agent_delivery 异常)→ 静默降级,不阻断主流程
- 触发场景:编排节点完成广播(orchestrate.py:127-132)/ `POST /v1/agents/{id}/messages`
