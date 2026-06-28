---
name: execution-history
target: http://localhost:3000/memory
tags: [smoke, edge]
timeout_ms: 120000
---

# 执行历史与回放

## 目标
验证 agent 执行历史(node 步骤事件)可观测,且可对某次执行发起回放,形成查看-回放闭环。

## 前置
- 执行过一次对话(/execute)或编排(/orchestrate),产生 execution_log 记录(log_execution_step)

## 步骤
1. (act) 执行一次对话或编排,产生执行历史
2. (act) 打开 /memory 页面并切换到 history tab
3. (observe) 查看执行历史列表(Run 分组,60s 间隔为新一轮 Run)
4. (observe) 查看某一 Run 的时间线条形图与错误计数
5. (extract) 抽取执行历史 API 返回的事件字段(含 node_id / status / input / output / timestamp,最新优先)
6. (act) 点击「回放此执行」按钮

## 权威信号
- /memory 的 history tab 可见执行历史面板
- 历史按 Run 分组显示(Run #N)
- 每个 Run 显示时间线条形图(绿/蓝/红节点状态)
- 存在错误时显示错误计数
- 每条 Run 记录存在「回放此执行」按钮
- 点击回放后,DebugPanel 触发该次执行回放
- 执行历史 API 返回事件结构含 id / node_id / agent_id / session_id / status / input / output / timestamp
- 无执行历史时,列表显示为空
