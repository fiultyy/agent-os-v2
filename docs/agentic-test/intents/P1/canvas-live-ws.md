---
id: canvas-live-ws
title: canvas 实时事件流(WS 连接 + 事件)
page: /canvas/live
api: WS /ws/canvas
priority: P1
---

# 意图
用户打开 canvas live 页面,建立 WebSocket 连接,实时观察 agent 执行事件流(node/tick/agent_message)。验证 canvas WS 实时通路(L3 通电项:WS + sessionId 闭环)。

# 前置
- 有一个有效 session_id(从对话或编排产生,非 "default")
- canvas WS 配置(token/origin;本地 localhost 默认允许)

# 步骤
1. 打开 `/canvas/live?session_id=<某个会话 id>`(或从对话/canvas 入口进入)
2. 观察 WS 连接状态(绿色 Wifi = 已连接)
3. 触发一个 agent 执行(对话/编排),观察实时事件流

# 验证
- UI:连接成功显示绿色 Wifi +「已连接: {sessionId}」;断开红色 WifiOff +「未连接」
- WS 事件流:`node_start` / `node_complete` / `tick` / `agent_message` 实时推送
- 历史回放:断线重连后 replay 历史事件(after_eventId 断点续传,不丢)
- 心跳:ping/pong 保活

# 失败模式
- token/origin 验证失败 → 关闭码 4001(auth)/ 4003(origin),不自动重连
- session_id="default" → 可能无事件或拒绝
- 网络中断 → 指数退避重连(1s→2s→4s→...→30s 上限)
- canvas 未初始化 → 关闭码 1011
