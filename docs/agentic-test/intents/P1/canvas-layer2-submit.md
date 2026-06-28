---
id: canvas-layer2-submit
title: canvas Layer2 提交(cmd 协议)
page: /canvas/live
api: WS layer2.submit
priority: P1
---

# 意图
用户在 canvas Layer2 面板暂存节点(text/command),提交给后端处理。验证 Layer2 提交协议(L3 通电项;**cmd 字段**修正:后端读 `cmd` 不是 `type`,这是已知的「方向写反」修正)。

# 前置
- 已建立 canvas WS 连接(见 canvas-live-ws)
- 有有效 session_id + branch_id

# 步骤
1. 打开 /canvas/live,确保 WS 已连接(绿色 Wifi)
2. 在 Layer2 面板添加节点(text 节点如「hello」,或 command 节点如「/help」)
3. 点击提交按钮

# 验证
- 提交后收到 `{"cmd":"layer2.submitted","status":"accepted"}`
- UI:Layer2 面板 staging 区清空
- 后端触发 `layer2.submitted` 事件(供其他消费者)
- 协议关键:前端发 `cmd:"layer2.submit"`(cmd 字段,非 type)

# 失败模式
- 缺 nodes/commands → 错误响应「layer2.submit requires 'nodes' or 'commands'」
- WS 断开时提交失败
- 节点类型:text(文本)/ command(以 `/` 开头的命令)
