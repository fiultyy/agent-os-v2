---
name: multi-turn-fc
target: http://localhost:3000/
tags: [smoke, lifecycle, api]
timeout_ms: 120000
status: ready
---

> IT-4.1 通电(8846438 Gap2 sse fetch streaming)+ IT-4.2(92385ae step-scoped)+ 31ae340(幻觉挡修)。
> 同 fc-tool-call 模式:首页 textarea/发送 button aria-label + selector hint(page.fill 触发 onChange)+ sse step 监听。
> default agent glm-4-flash 多轮 tool_use 支持未确认 + DNS 可能临时故障;工具调用信号 conditional,主要验证 sse 通路捕事件序列。

# 多轮工具调用循环(tool→llm→synthesize)

## 目标
验证 sse step 监听多轮 SSE 事件序列(node_start/tool/node_complete 交替);LLM 多轮调工具时 tool_result 回注下一轮。

## 前置
- 已存在至少一个 agent(default 85f484f5)
- LLM 支持多轮 function-calling(conditional)

## 步骤
1. (act) 打开应用首页(default agent 自动选中)
2. (act) 在消息输入框填写需要多步工具的问题 || textarea[aria-label="消息输入"] :: 读取 /tmp/test.txt 的内容然后总结
3. (act) 点击发送消息按钮 || button[aria-label="发送消息"]
4. (sse) 监听对话 SSE 流 ~90s 出现多轮 node_start/node_complete 事件序列与最终回答

## 权威信号
- SSE 事件序列出现 node_start 与对应的 node_complete(证明 sse step 捕到前端 fetch streaming SSE)
- SSE 流最终出现综合输出/最终回答(execution_complete 或最终 node_complete)
- 多轮 node_start 交替出现 —— conditional,若 LLM 多轮调工具
- 每轮 tool_result 回注下一轮 —— conditional
- 若 default agent 模型不支持多轮 tool_use 或 DNS 故障,工具调用类信号放宽(主要验证 sse 通路)
