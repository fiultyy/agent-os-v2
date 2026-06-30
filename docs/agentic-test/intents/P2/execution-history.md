---
name: execution-history
target: http://localhost:3000/memory
tags: [smoke, edge]
timeout_ms: 120000
status: NOT-WIRED
---

# 执行历史与回放

> ⚠️ NOT-WIRED: /memory 无 history tab(只有 memory/communication/debug/orchestration 4 tab,memory/page.tsx:16-21);Run 分组 / Run #N / 时间线条形图 / 错误计数 /「回放此执行」按钮 UI 均未实现。当前最接近的是「调试」tab 的 `<DebugPanel>`(扁平 event timeline,debugStore.executionEvents slice(-200),DebugPanel.tsx:160-194、266-282;执行历史 API 为 gateway /debug/history proxy orchestrator)。要么重写本 intent 对齐 DebugPanel,要么前端补 Run 分组 UI。

## 目标
验证 agent 执行事件(node 步骤事件)在「调试」tab 可观测,且可对单条事件发起 replay 查看 input/output,形成查看-回放闭环。

## 前置
- 执行过一次对话(/execute)或编排(/orchestrate),产生 executionEvents 记录(debugStore.executionEvents 非空)

## 步骤
1. (act) 执行一次对话或编排,产生事件流(executionEvents)
2. (act) 打开 /memory 页面并切换到「调试」tab
3. (observe) 查看 timeline 显示的 node 事件列表(每条带 running / done / error 状态图标)
4. (act) 点击某条事件,setReplayIndex 进入该事件 replay 详情
5. (observe) 查看 replay 详情中的 input / output / executionTimeMs 字段
6. (act) 操作顶部 replay 控件(Pause / SkipBack / SkipForward / RotateCcw)前后步进

## 权威信号
- /memory 的「调试」tab 可见 DebugPanel 事件面板
- timeline 按 executionEvents 顺序显示 node 事件(running / done / error 状态图标)
- 点击某条事件后,replay 详情展示该事件的 input / output / executionTimeMs
- 顶部 replay 控件(Pause / SkipBack / SkipForward / RotateCcw)可前后步进
- executionEvents 为空时,timeline 显示空状态文案「开启调试模式以记录事件」
- /debug/history API(orchestrator proxy)返回事件结构含 id / node_id / agent_id / session_id / status / input / output / timestamp
