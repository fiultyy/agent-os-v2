---
name: canvas-live-ws
target: http://localhost:3000/canvas/live
tags: [smoke, lifecycle, canvas]
timeout_ms: 120000
---

# Canvas 实时事件流(WebSocket 连接 + 事件)

## 目标
验证 canvas live 页面能建立 WebSocket 连接,实时展示 agent 执行事件流(node/tick/agent_message)。

## 前置
- 有一个有效 session_id(从对话或编排产生,非 "default")
- canvas WS 配置可用(token/origin;本地 localhost 默认允许)

## 步骤
1. (act) 打开 canvas live 页面(带有效 session_id 参数)
2. (observe) 查看 WS 连接状态指示器(绿色 Wifi 图标 + 已连接文案)
3. (act) 触发一个 agent 执行(对话或编排发起)
4. (observe) 查看实时事件流推送
5. (extract) 抽取 WS 推送的事件类型(如 node_start / node_complete / tick / agent_message)
6. (observe) 断开连接后查看重连与历史回放(重连后重放该 session 全部历史事件,可能重复;after_event_id query param 当前被初始连接忽略,B1 的 after_id 修复仅 replay 命令路径生效,wsClient 重连走 query param 不发命令)

## 权威信号
- 连接成功后显示绿色 Wifi 图标及「已连接: {sessionId}」文案
- 断开时显示红色 WifiOff 图标及「未连接」文案
- 事件流实时推送 node_start / node_complete / tick / agent_message 等事件
- 断线重连后历史事件被回放(重连后重放该 session 全部历史事件,可能重复;after_event_id query param 当前被初始连接忽略,B1 的 after_id 修复仅 replay 命令路径生效,wsClient 重连走 query param 不发命令)
- 心跳保活机制运行(WS 连接保持存活)(deferred: wsClient 无周期 ping)
- 认证失败时连接关闭码为 4001,不自动重连
- session_id="default" 或为空时连接关闭码为 4002
- origin 验证失败时连接关闭码为 4003,不自动重连
- canvas 未初始化时连接关闭码为 1011

## 注
> 实现_bug: canvas.py:182 初始连接应读 `after_event_id=ws.query_params.get("after_event_id")` 传入 `replay(session_id, ws, after_event_id)`,断点续传才真生效(当前 B1 修复对 wsClient 重连路径无效)
