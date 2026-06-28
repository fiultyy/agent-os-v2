---
name: canvas-layer2-submit
target: http://localhost:3000/canvas/live
tags: [smoke, lifecycle, canvas]
timeout_ms: 120000
---

# canvas Layer2 提交(cmd 协议)

## 目标
验证 canvas Layer2 面板可暂存 text/command 节点并提交,后端按 `cmd` 字段接受协议层(L3 通电项;已知「方向写反」修正:后端读 `cmd` 不是 `type`)。

## 前置
- canvas WS 连接已建立(状态指示为已连接)
- 存在有效 session_id + branch_id

## 步骤
1. (observe) 打开 /canvas/live 页面,确认 WS 连接状态指示显示为已连接(如绿色连接标记)
2. (act) 在 Layer2 面板暂存区添加一个节点(text 节点填「hello」,或 command 节点填「/help」)
3. (act) 点击 Layer2 面板的提交按钮
4. (extract) 抽取提交后的协议响应,确认包含 cmd 为 layer2.submitted 且 status 为 accepted 的 JSON 结构

## 权威信号
- 页面 Layer2 面板的暂存区在提交后清空(无残留节点)
- 提交后出现表示接受的响应文本,包含 "accepted" 字样
- 响应 JSON 的命令字段为 "layer2.submitted"(而非 layer2.submit 请求名)
- WS 断开时,提交后出现错误提示(含 "requires 'nodes' or 'commands'" 文案)
