---
name: execution-history
target: http://localhost:3000/memory
tags: [smoke, edge]
timeout_ms: 60000
status: ready
---

# 执行历史与回放

> 验证 /memory 历史 tab 的 DebugPanel 结构(面板 + replay 控件 + 空状态 + /debug/history API)。events timeline 回放需 debugMode 开启 + 跑对话产生 events,跨 intent,本 intent 只验证结构存在性。

## 目标
验证 DebugPanel 事件面板可观测,replay 控件(Pause/SkipBack/SkipForward/RotateCcw)存在,空状态文案正确,/debug/history API 返回合规事件结构。

## 前置
- 系统已启动(前端 3000 + 后端 8000)

## 步骤
1. (act) 打开 /memory 页面,点击侧边栏「历史」tab(History 图标)|| button[aria-label="历史"] ::
2. (observe) 确认 DebugPanel 事件面板可见(标题 / 开启调试按钮 / 空状态)
3. (observe) 确认顶部 replay 控件按钮存在(Pause / SkipBack / SkipForward / RotateCcw)
4. (observe) 确认 executionEvents 为空时显示空状态文案「开启调试模式以记录事件」

## 权威信号
- /memory 的「历史」tab 可见 DebugPanel 事件面板
- DebugPanel 顶部有 replay 控件(Pause / SkipBack / SkipForward / RotateCcw 图标按钮)
- executionEvents 为空时,timeline 显示空状态文案「开启调试模式以记录事件」
- /debug/history API(gateway proxy)返回事件结构(数组,条目含 node_id / status 等字段,空也是 200 数组)
