---
name: execution-history
target: http://localhost:3000/memory
tags: [smoke, edge]
timeout_ms: 60000
status: ready
---

# 执行历史与回放

> 验证 /memory 历史 tab 的 DebugPanel 结构(面板 + 调试开关 + 空状态)+ /v1/debug/history API。
> events timeline 回放需 debugMode 开启 + 跑对话产生 events(跨 intent),本 intent 只验证结构存在性,不要求 replay 控件(需 debugMode 开启才渲染)。

## 目标
验证 DebugPanel 事件面板可观测,调试模式开关存在,空状态文案正确,/v1/debug/history API 返回合规事件结构。

## 前置
- 系统已启动(前端 3000 + 后端 8001)

## 步骤
1. (act) 打开 /memory 页面,点击侧边栏「历史」tab(History 图标)|| button[aria-label="历史"] ::
2. (observe) 确认 DebugPanel 事件面板可见(标题 / 开启调试按钮 / 空状态)
3. (observe) 确认调试模式开关存在(「开启调试」按钮,debugMode 关闭时显示)
4. (observe) 确认 executionEvents 为空时显示空状态文案「开启调试模式以记录事件」
5. (api) GET http://localhost:8001/v1/debug/history ||| 抽取事件结构(数组,空或含 node_id/status)

## 权威信号
- [step 2] /memory 的「历史」tab 可见 DebugPanel 事件面板
- [step 3] DebugPanel 有调试模式开关(「开启调试」按钮存在)
- [step 4] executionEvents 为空时,timeline 显示空状态文案「开启调试模式以记录事件」
- [step 5] GET /v1/debug/history(orchestrator entities.py:38)返回 200 + JSON 数组(空或事件条目)
