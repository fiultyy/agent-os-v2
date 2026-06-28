---
id: memory-recall
title: 对话/编排后召回记忆
page: /memory(记忆 tab)
api: GET /v1/memories
priority: P0
---

# 意图
用户想查看 agent 沉淀的记忆(对话或编排后产生),验证记忆系统工作 —— 这是 MVP 北极星"记忆召回"的可观测出口,也是 ADR-3 编排记忆闭环的召回侧验证。

# 前置
- 已与某 agent 对话(single-agent-chat)或编排过(multi-agent-orchestrate),产生记忆沉淀

# 步骤
1. 打开 /memory 页面,切到「记忆」tab
2. 选一个 agent(对话/编排过的那个)
3. 输入查询词(如对话/编排的主题关键词)
4. 触发召回

# 验证
- UI:记忆面板显示召回的记忆项(content 摘要 + agent_id + memory_type)
- API:`GET /api/memories?agent_id=<X>&query=<关键词>` 返回记忆列表(非空)
- 隔离性:换另一个 agent_id 查询,不返回 X 的记忆(agent_id 严格过滤,召回侧闭环 72b4386)
- ADR-3:编排 orchestrator 的记忆按 orchestrator_id 可召回(沉淀→召回闭环)

# 失败模式
- agent 无历史(未对话/编排)→ 空列表(正常)
- query 不匹配关键词 → 空或低相关
- 中文 query 经前端需 URL encode(curl 裸中文 → "Invalid HTTP request";浏览器正常)
