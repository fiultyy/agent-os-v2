---
name: multi-agent-orchestrate
target: http://localhost:3000/memory
tags: [smoke, core]
timeout_ms: 120000
status: NOT-WIRED
---

# 多 agent 编排(researcher+writer→综合)

> NOT-WIRED: gateway 缺 /orchestrate 代理(main.py 无 router,routes/ 无 orchestrate.py),前端 POST /api/orchestrate → gateway:8000/orchestrate → 404。后端 orchestrator:8001/v1/orchestrate 通。待 gateway 加 routes/orchestrate.py 代理(POST /orchestrate → ORCHESTRATOR_API/orchestrate)+ main.py 注册 prefix=/orchestrate

## 目标
验证 orchestrator 能调度 researcher+writer 子 agent 并行处理任务,fan-in 汇聚后由 synthesizer 综合成融合两视角的答案。

## 前置
- 已创建一个 orchestrator agent(作综合者),用于在下拉中选择
- 后端 `/v1/orchestrate` 端点可用(anthropic 协议 + glm-4.7)

## 步骤
1. (act) 打开 /memory 页面,切到「编排」tab(第 4 个 tab)
2. (act) 在下拉中选择 orchestrator agent
3. (act) 输入任务"一句话介绍光合作用"
4. (act) 配置 sub_agents:默认为 `researcher`(研究员,提供事实)与 `critic`(评论员,审视),各填 role + system_prompt(默认非 writer)
5. (act) 点击「触发编排」按钮
6. (observe) 查看 SSE 实时事件流的输出

## 权威信号
- 编排面板显示 `multi_agent` 的 branches:researcher 与 critic 各自产生 output
- 编排面板显示 `fan_in` 汇聚节点
- 编排面板显示 `synthesizer` 综合输出(融合 researcher 事实视角与 critic 审视视角的答案)
- 事件流中出现 `execution_complete.output` 事件,含综合答案文本
- 编排完成后,查询 orchestrator 的记忆列表可见沉淀记忆(episodic/agent 类型)
