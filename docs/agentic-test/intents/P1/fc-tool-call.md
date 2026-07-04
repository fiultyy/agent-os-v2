---
name: fc-tool-call
target: http://localhost:3000/
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: ready
---

> IT-4.1 通电(8846438 Gap2):collectSse injectSseHook(包装 window.fetch,tee text/event-stream 到 __qaSseChunks)。
> IT-4.2(92385ae step-scoped)+ Gap2 诊断(e654739 __qaFetchLog)。
> 首页 textarea/发送 button 加 aria-label(本次),selector hint 用原生 valueSetter fill(治 React 受控 stagehand fill 不入 → input 空 → 发送 button disabled "No action found")。
> ⚠️ default agent glm-4-flash tool_use 支持未确认;sse step 主要验证通路捕事件,工具调用信号 conditional。

# 对话中 LLM 调工具(function-calling 单轮)

## 目标
验证 LLM 面对需要工具的问题时,通过原生 function-calling 调用工具;并验证 sse step 监听前端 fetch streaming SSE(Gap2 injectSseHook)。

## 前置
- 已存在至少一个 agent(default 85f484f5)
- LLM 支持 function-calling(glm-4-flash 经 anthropic 通道支持未确认)

## 步骤
1. (act) 打开应用首页(default agent 自动选中)
2. (act) 在消息输入框填写需要工具的问题 || textarea[aria-label="消息输入"] :: 读取 /tmp/test.txt 的内容
3. (act) 点击发送消息按钮 || button[aria-label="发送消息"]
4. (sse) 监听对话 SSE 流 ~90s 出现 node_start/node_complete 事件序列与工具调用、最终回答

## 权威信号
- SSE 事件序列出现 node_start 与对应的 node_complete(证明 sse step 捕到前端 fetch streaming SSE,Gap2 工作)
- 事件中出现工具调用节点 —— conditional,若 LLM 调工具
- 工具调用后出现工具结果(tool_result)事件 —— conditional
- 最终回答基于工具结果 —— conditional
- 若 default agent 模型不支持 tool_use,工具调用类信号放宽(主要验证 sse 通路)
