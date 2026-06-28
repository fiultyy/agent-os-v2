---
id: multi-agent-orchestrate
title: 多 agent 编排(researcher+writer→综合)
page: /memory(编排 tab)
api: POST /v1/orchestrate
priority: P0
---

# 意图
用户想让多个子 agent(researcher+writer)并行处理一个任务,fan-in 汇聚,synthesizer 综合成多视角答案。这是多 agent 编排里程碑(ADR-1/2/3 闭环)的核心用户价值。

# 前置
- 已创建一个 orchestrator agent(作综合者,选它)
- 后端 `/v1/orchestrate` 端点在(8000,anthropic 协议+glm-4.7)

# 步骤
1. 打开 /memory 页面,切到「编排」tab(第4 tab)
2. 选 orchestrator agent(下拉)
3. 输入任务"一句话介绍光合作用"
4. 配置 sub_agents:`researcher`(研究员,提供事实)+ `writer`(作家,润色)—— 各填 role + system_prompt
5. 点击「触发编排」
6. 观察 SSE 实时事件流

# 验证
- UI:编排面板显示 `multi_agent` 的 branches(researcher/writer 各自 output)→ `fan_in` 汇聚 → `synthesizer` 综合输出
- API:`POST /api/orchestrate` SSE 含 `execution_complete.output`(综合答案,融合两视角)
- ADR-3 闭环:编排后 `GET /api/memories?agent_id=<orchestrator>` 含 orchestrator 沉淀记忆(episodic/agent)
- 自召回注入:第2次编排时 orchestrator 召回第1次记忆(综合轮 retrieve,日志 `orchestrator self-recall: hits=N`)

# 失败模式
- 未选 orchestrator → 404(orchestrator not found)
- sub_agents 空 → 400
- LLM 429(资源包不足)→ branches output 含 error;glm-4.7 须走 anthropic 协议(/api/anthropic coding plan),openai paas/v4 会 429
