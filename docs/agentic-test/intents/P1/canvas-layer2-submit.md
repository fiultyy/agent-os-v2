---
name: canvas-layer2-submit
target: http://localhost:3000/canvas/live?session_id=qa-layer2
tags: [smoke, lifecycle, canvas]
timeout_ms: 120000
status: ready
---

> IT-4 通电(DOM 验证层):Layer2Panel 已实现(layer2Store staging/addNode/clear/submitPayload)。
> handleSubmit:canvasWsClient.send(payload) → **立即 clear()**(不等 server 响应),故 submit 后暂存清空是 DOM 可见。
> server 的 layer2.submitted accepted 响应在 WS 帧返回(Layer2Panel 不渲染,只 console.log)→ DOM 读不到。
> 主信号 = DOM 可观测(暂存 Add 显示节点 + Submit 清空);WS 协议响应 deferred(collectWs page.on 捕不到已建立 WS,同 canvas-live-ws gap)。

# canvas Layer2 提交(暂存 + 清空 DOM 流)

## 目标
验证 canvas Layer2 面板可暂存 text/command 节点并提交(触发 wsClient.send),提交后暂存区清空。

## 前置
- web :3000 + orchestrator :8001 健康
- target 带 session_id=qa-layer2(wsClient 自动连接,Layer2Panel 渲染)

## 步骤
1. (wait) ~8s 等 WS 连接建立(Wifi 绿 + 已连接)
2. (act) 点击 Text 类型按钮 || button:has-text("Text")
3. (act) 在 textarea 填节点内容 || textarea[placeholder="Type..."] :: hello
4. (act) 点击 Add 暂存节点 || button:has-text("Add")
5. (wait) ~3s 等暂存渲染
6. (observe) 暂存区显示 hello 节点(非 Empty)
7. (act) 点击 Submit 提交 || button:has-text("Submit")
8. (wait) ~3s 等提交 + clear
9. (observe) 暂存区清空(显示 Empty)

## 权威信号
- WS 连接成功(Wifi 绿 + 已连接,Layer2Panel 渲染前提)
- Add 后暂存区显示节点(显示 "hello" 内容,非 Empty 状态)
- Submit 后暂存区清空(显示 Empty,证明 handleSubmit 触发 clear)

## 注(deferred)
> WS 协议响应(layer2.submitted accepted JSON)在 WS 帧返回,Layer2Panel 不渲染 → DOM 读不到。
> 需 collectWs 监听 wsClient.send(layer2.submit)→ server 回 layer2.submitted,但 collectWs page.on 捕不到
> 已建立 WS(同 canvas-live-ws gap),需 #6 改 CDP Network.webSocketFrameReceived。
> cmd 字段(layer2.submitted 非 layer2.submit)是后端"方向写反"修正,需 WS 帧验证确认。
