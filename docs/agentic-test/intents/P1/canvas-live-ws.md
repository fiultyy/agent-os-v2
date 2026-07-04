---
name: canvas-live-ws
target: http://localhost:3000/canvas/live?session_id=qa-ws-test
tags: [smoke, lifecycle, canvas]
timeout_ms: 120000
status: ready
---

> IT-4 通电(DOM 验证层):CanvasLivePanel 已实现(c6a6a31)+ wsClient 心跳保活(onopen ping+30s 周期,治 c6a6a31 deferred 心跳项)。
> runtime 先 goto(target) 打开页面;wsClient 自动连接,server accept + replay(session_id) 推历史帧。
> 主信号 = DOM 可观测的连接状态(Wifi 绿 + 已连接)+ 事件流面板。
> ws 帧验证 deferred:collectWs 用 `page.on('websocket')` 只捕**新建** WS,而 wsClient 在 goto 时(page load)
> 已建立连接 → step 注册晚捕不到已建立 WS 对象 → 收不到 framereceived(无论心跳 pong 多密)。需 #6 改
> collectWs 用 CDP `Network.webSocketFrameReceived`(捕已建立 WS 帧)后,加回 ws step 验证双向通路。

# Canvas 实时事件流(WebSocket 连接)

## 目标
验证 canvas live 页面能建立 WebSocket 连接,展示连接状态 + 事件流面板。

## 前置
- web :3000 + orchestrator :8001 健康
- target 带 session_id=qa-ws-test(任意非空;canvas.py 验证非空,空 → close 4002)

## 步骤
1. (wait) ~10s 等 WS 连接建立,顶部显示绿色 Wifi 图标 + "已连接" 文案
2. (observe) 查看 WS 连接状态指示器(绿色 Wifi 图标 + "已连接: qa-ws-test" 文案)
3. (observe) 查看事件流面板可见("事件流" 标题渲染 + 计数)

## 权威信号
- 绿色 Wifi 图标 + "已连接: qa-ws-test" 文案(WS 连接成功建立,server accept + wsClient onopen 触发 store.setConnected)
- 事件流面板可见("事件流" 标题渲染,CanvasLivePanel 挂载)
- (deferred) ws 帧验证(replay/live 事件 或 心跳 pong):collectWs page.on 捕不到已建立 WS,需 #6 改 CDP

## 注
> WS 错误码场景(认证失败 4001 / origin 拒 4003 / session 空 4002 / canvas 未初始化 1011)需主动构造异常条件,
> 浏览器自动流难精确触发,deferred。断线重连回放 B1 修复对 wsClient 重连路径无效,deferred。
