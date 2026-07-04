---
name: canvas-layer2-submit
target: http://localhost:3000/canvas/live?session_id=qa-layer2
tags: [smoke, lifecycle, canvas]
timeout_ms: 90000
status: ready
---

> IT-4 通电(DOM 验证层):Layer2Panel 已实现 + aria-label(textarea/Add/Submit,本次加,selector 更稳)。
> handleSubmit:canvasWsClient.send(payload) → 立即 clear(),submit 后暂存清空 DOM 可见。
> server layer2.submitted 响应在 WS 帧不渲染 → DOM 读不到,WS 协议响应 deferred。
> 删 wait step(避免 runtime wait LLM 二分类判"暂存渲染"超时 475s 异常),用 observe 一次性读。
> Text 类型默认(inputType="text" 初值),不需点 Text button。

# canvas Layer2 提交(暂存 + 清空 DOM 流)

## 目标
验证 canvas Layer2 面板可暂存 text 节点并提交(触发 wsClient.send),提交后暂存区清空。

## 前置
- web :3000 + orchestrator :8001 健康
- target 带 session_id=qa-layer2(wsClient 自动连接,Layer2Panel 渲染)

## 步骤
1. (wait) ~8s 等 WS 连接建立(Wifi 绿 + 已连接)
2. (act) 在 textarea 填节点内容 || textarea[aria-label="Layer2 节点内容"] :: hello
3. (act) 点击 Add 暂存节点 || button[aria-label="暂存节点到 Layer2"]
4. (observe) 暂存区显示 hello 节点(非 Empty 状态)
5. (act) 点击 Submit 提交 || button[aria-label="提交 Layer2"]
6. (observe) 暂存区清空(显示 Empty)

## 权威信号
- WS 连接成功(Wifi 绿 + 已连接,Layer2Panel 渲染前提)
- Add 后暂存区显示节点(显示 "hello" 内容,非 Empty)
- Submit 后暂存区清空(显示 Empty,证明 handleSubmit 触发 clear)

## 注(deferred)
> WS 协议响应(layer2.submitted accepted JSON)在 WS 帧返回,Layer2Panel 不渲染 → DOM 读不到。
> 需 collectWs 监听 wsClient.send(layer2.submit)→ server 回 layer2.submitted。IT-4.1 Gap3(8846438)collectWs 全 CDP
> 已修(捕已建立 WS 帧),但 canvas-layer2 的 layer2.submit 由 act 触发(新建 send),需 ws step 在 act 后监听
> 捕 send 后的 server 响应帧 —— 后续可加 (ws) 监听 step 验证协议层。
