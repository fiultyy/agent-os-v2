---
name: multi-agent-orchestrate
target: http://localhost:3000/memory
tags: [smoke, core]
timeout_ms: 120000
status: ready
---

# 多 agent 编排(researcher+critic→综合)

> gateway /orchestrate 代理已通(commit 6a41d17):前端 POST /api/orchestrate → gateway /orchestrate → orchestrator /v1/orchestrate SSE 透传。R2 contract 修复(0527b59)治编排子节点 1214(researcher/critic 正常 output)。

## 目标
验证 orchestrator 能调度 researcher+critic 子 agent 并行处理任务,fan-in 汇聚后由 synthesizer 综合成融合两视角的答案。

## 前置
- 已创建一个 orchestrator agent(作综合者),用于在 radio 中选择
- 后端 `/v1/orchestrate` 端点可用(anthropic 协议 + glm-4.7)

## 步骤
1. (act) 打开 /memory 页面,点击左侧侧边栏的「编排」icon 按钮(侧边栏从上到下:记忆/通信/调试/编排) || button[aria-label="编排"] ::
2. (observe) 确认编排 panel 已渲染(页面可见「Orchestrator Agent」标签 或 编排任务输入框,证明 tab 切对了;若看不到说明 act #0 没切 tab)
3. (act) 选择第一个 orchestrator agent(radio 按钮,点第一个) || button[role="radio"] ::
3. (act) 在编排任务输入框输入任务 || textarea[aria-label="编排任务输入"] :: 一句话介绍光合作用
4. (observe) 确认 sub_agents 区默认有 `researcher` 与 `critic` 两个角色条目(前端 OrchestrationPanel 默认填充,无需手动配置)
5. (act) 点击触发编排按钮 || button[aria-label="触发编排"] ::
6. (wait) 等待 execution_complete 事件(约 60-120s,fan-out → fan-in → synthesizer 综合)

## 权威信号
- 编排面板显示 `multi_agent` 的 branches:researcher 与 critic 各自产生 output
- 编排面板显示 `fan_in` 汇聚节点
- 编排面板显示 `synthesizer` 综合输出(融合 researcher 事实视角与 critic 审视视角的答案)
- 编排区最终显示完整综合答案文本(execution_complete 后 synthesizer 输出完整可见,DOM 层断言非 SSE 事件层)
