---
name: memory-recall
target: http://localhost:3000/memory
tags: [smoke, core, api]
timeout_ms: 120000
---

# 记忆召回验证

## 目标
验证用户能查看到 agent 沉淀的记忆(对话或编排后产生),召回面板返回相关记忆项且按 agent 严格隔离。

## 前置
- 已与某 agent 对话过或编排过,产生记忆沉淀

## 步骤
1. (act) 打开 /memory 页面,切到「记忆」tab
2. (act) 选择一个已对话/编排过的 agent
3. (act) 在查询输入框填写查询词(对话/编排的主题关键词)
4. (act) 搜索框输入关键词,前端对已加载记忆做 content 子串过滤
5. (observe) 查看记忆面板返回的召回结果
6. (extract) 抽取召回记忆项的 content 摘要、agent_id、memory_type
7. (observe) 查看空状态情形:若该 agent 无历史(未对话/编排),面板显示空列表
8. (act) 切换查询另一个 agent_id,触发召回
9. (extract) 抽取该 agent 的召回结果,确认不含原 agent 的记忆项(agent_id 严格隔离)

## 权威信号
- 记忆面板显示召回的记忆项(content 摘要 + agent_id + memory_type)
- 客户端过滤后列表缩小至含关键词项(对该 agent 输入匹配的关键词时)
- 换另一个 agent_id 查询时,返回结果不含原 agent 的记忆(agent_id 严格过滤)
- agent 无历史时显示空列表(正常空状态,非报错)

## 注
> 后端 /v1/memories?query= recall(memory.py:162-181,走 RetrieverAgent)能力具备但前端 getMemories 未传 query,未接(deferred)
