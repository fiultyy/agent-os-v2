---
id: execution-history
title: 执行历史(node_* 事件 + 回放)
page: /memory(history tab)
api: GET /v1/debug/history
priority: P2
---

# 意图
用户查看 agent 执行历史(node 步骤),可回放某次执行。验证 execution_log 可观测 + 回放闭环。

# 前置
- 执行过对话(/execute)或编排(/orchestrate),产生 execution_log 记录(log_execution_step)

# 步骤
1. 执行一次对话或编排(产生历史)
2. 打开 /memory 的 history tab(ExecutionHistoryPanel)
3. 查看 Run 分组(60s 间隔 = 新 run),点击「回放此执行」

# 验证
- API:`GET /v1/debug/history?agent_id=&session_id=&limit=50` 返回 `[{id, node_id, agent_id, session_id, status, input, output, timestamp}]`(最新优先)
- UI:ExecutionHistoryPanel 显示 Run #N + 时间线条形图(绿/蓝/红)+ 错误计数 + 回放按钮
- 回放:setReplayIndex 触发 DebugPanel 回放该执行

# 失败模式
- execution_log 纯内存(MAX 1000 条 circular)→ 重启丢失
- 无执行历史 → 空列表
- 60s 无新事件 = 新 run 分组(GAP_MS)
