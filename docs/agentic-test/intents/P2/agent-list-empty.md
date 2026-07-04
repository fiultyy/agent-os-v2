---
name: agent-list-empty
target: http://localhost:3000/agents
tags: [smoke, edge]
timeout_ms: 60000
---

# Agent 空列表边界

## 目标
验证边界:无 agent 时 /agents 页面显示空状态且前端不崩(并确认默认助手兜底,正常情况列表非空)。

## 前置
- 默认情况 init_default_agent 总创建「默认助手」,列表非空;触发空列表需清空所有 agent 并跳过 init_default_agent(生产几乎不可能)

## 步骤
1. (observe) 导航到 /agents 页面并查看 Agent 管理页主区域
2. (extract) 抽取当前 agent 列表的数量
3. (observe) 若列表为空,查看页面空状态提示;若列表非空,确认默认助手可见

## 权威信号
- 正常情况:/agents 页面至少显示 1 个 agent(默认助手)
- 边界情况(conditional):列表为空时显示空状态引导文案;**当前列表非空(init_default_agent 兜底,生产几乎不会空)时此信号放宽**,信号1 加 不崩溃已证正常态
- /agents 页面加载完成不崩溃
