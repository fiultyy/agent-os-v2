---
name: canvas-live-ws
target: http://localhost:3000/canvas/live?session_id=qa-ws-test
tags: [smoke, lifecycle, canvas]
timeout_ms: 120000
status: ready
---

> IT-4 通电(v0.5 ws step + CanvasLivePanel 已实现 c6a6a31):浏览器内 `page.on('websocket')` 监听 canvas WS 帧。
> wsClient.ts:27 `new WebSocket(ws://localhost:3000/ws/canvas?session_id=...)` → next rewrite → orchestrator :8001 canvas.py。
> 连接成功后 server `replay(session_id)` 推历史帧(canvas.py:183);新 session 无历史则 0 replay 帧,ws step ok 取决于是否有 live 推送。
> 主信号 = DOM 可观测的连接状态(Wifi 绿 + "已连接"),ws 帧为辅助证据。

# Canvas 实时事件流(WebSocket 连接)

## 目标
验证 canvas live 页面能建立 WebSocket 连接,展示连接状态 + 事件流面板。

## 前置
- web :3000 + orchestrator :8001 健康
- target 带 session_id=qa-ws-test(任意非空;canvas.py 验证非空,session_id="default" 或空 → close 4002)

## 步骤
1. (act) 打开 canvas live 页面(target 已带 session_id 参数,wsClient 自动连接)
2. (observe) 查看 WS 连接状态指示器(绿色 Wifi 图标 + "已连接" 文案)
3. (ws) 监听 canvas WS ~10s 收到事件帧(replay 历史或 live 推送)
4. (observe) 查看事件流面板可见("事件流" 标题 + 计数)

## 权威信号
- 绿色 Wifi 图标 + "已连接: qa-ws-test" 文案(WS 连接成功建立)
- 事件流面板可见("事件流" 标题渲染)
- ws 监听期内观测到 ws 活动(收到 replay 历史帧 或 live 事件帧;新 session 无历史时此信号可放宽)

## 注
> WS 错误码场景(认证失败 4001 / origin 拒 4003 / session 空 4002 / canvas 未初始化 1011)需主动构造异常条件,
> 浏览器自动流难精确触发,deferred。心跳(wsClient 周期 ping)deferred(canvas.py 已支持 ping/pong,wsClient 未发)。
> 断线重连回放:B1 修复(787896e)对 wsClient 重连路径(query param)无效,仅 replay 命令路径生效,deferred。
