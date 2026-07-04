---
name: memory-recall
target: http://localhost:3000/memory
tags: [smoke, core, api]
timeout_ms: 60000
status: NOT-WIRED
---

# 记忆召回验证

> NOT-WIRED(部分):记忆列表显示工作(extract #5 pass)+ 客户端 content 过滤工作;但**后端 query 召回 + agent_id 严格隔离**未接 —— 前端 getMemories(api.ts:333)第 3 参数 query 未传(MemoryPanel line 67 传 undefined),只客户端过滤,后端 RetrieverAgent recall(memory.py:162)能力具备但前端未调用。需 frontend 工程:MemoryPanel 查询触发 getMemories(query)接后端召回。intent step 8 切换 agent_id 召回因此 timeout(前端不发 query)。

## 目标
验证记忆面板显示 agent 记忆列表 + 客户端过滤(后端 query 召回 deferred)。

## 前置
- 已与某 agent 对话过或编排过,产生记忆沉淀

## 步骤
1. (act) 打开 /memory,切到「记忆」tab || button[aria-label="记忆"] ::
2. (observe) 确认记忆面板显示记忆列表(content 摘要 + memory_type)
3. (act) 在过滤输入框填写关键词(对话主题)
4. (observe) 客户端过滤后列表缩小至含关键词项
5. (extract) 抽取记忆项的 content 摘要、agent_id、memory_type

## 权威信号
- 记忆面板显示记忆项(content 摘要 + agent_id + memory_type)
- 客户端过滤后列表缩小至含关键词项
- (deferred) 后端 query 召回 + agent_id 严格隔离:前端未接 getMemories(query)
