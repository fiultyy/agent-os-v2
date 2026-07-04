---
name: memory-recall
target: http://localhost:3000/memory
tags: [smoke, core, api]
timeout_ms: 60000
status: ready
---

> IT-4 通电:getMemories(query) 已接(c6a6a31)——api.ts:333 第 5 参数 query 传递 + MemoryPanel 500ms 防抖(useRef+setTimeout)+ 客户端过滤双保险。
> 后端 RetrieverAgent recall(memory.py:162)能力具备,前端 query 触发后端召回 + agent_id 隔离。
> 前端流直接跑(observe/wait 读渲染),非 sse/ws。

# 记忆召回验证

## 目标
验证记忆面板显示 agent 记忆列表 + 过滤触发后端 query 召回 + agent_id 严格隔离。

## 前置
- 已与某 agent 对话过或编排过,产生记忆沉淀(无记忆时列表为空,过滤仍可验证不崩)

## 步骤
1. (act) 打开 /memory,切到「记忆」tab || button[aria-label="记忆"] ::
2. (observe) 确认记忆面板显示记忆列表(content 摘要 + memory_type)
3. (act) 在过滤输入框填写关键词 || input[placeholder*="Search"] :: 测试
4. (wait) ~6s 等待 500ms 防抖 + 后端召回返回(页面列表更新)
5. (observe) 客户端 + 后端过滤后列表更新(含关键词项 或 显示无匹配)
6. (extract) 抽取记忆项的 content 摘要、agent_id、memory_type

## 权威信号
- 记忆面板显示记忆项(content 摘要 + agent_id + memory_type)或空列表提示(无记忆时不崩)
- 过滤框填关键词后,经 500ms 防抖 + 后端召回,列表更新(含关键词项 或 无匹配提示)
- 过滤不崩(空列表 / 无匹配场景正常显示)
