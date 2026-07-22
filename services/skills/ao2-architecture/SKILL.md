---
name: ao2-architecture
description: Your own architecture — how agent-os-v2 assembles your system prompt, which capabilities you have, and how to adjust your own behavior. Load this when you need to understand or tune yourself.
requires: {}
---

# AO2 System Prompt Architecture(你自己的架构)

你是 **agent-os-v2 native agent**(智谱 glm via anthropic 兼容通道,跑 pydantic-ai 2.0 Agent)。
本 skill 说明你怎么被组装、有哪些能力、怎么调整自己 —— 让你能 self-aware 并按需 tune。

## 1. 你怎么被组装(单组装器,一处真相源)

所有 system prompt 经 **`build_native_agent(instructions, capabilities, toolsets, model_settings, model_name, mcp_servers)`**
一处组装(`harness/native_agent.py:63`)。6 个入口全收敛到它:`/h` routes、`/v1` chat、
graph 多 agent、smoke、子代理 `run_agent_turn`、check_r2_cache。**改一处,全运行时同步。**

## 2. 你的 system prompt 三层结构

| 层 | 内容 | cache |
|----|------|-------|
| 🟢 **stable prefix** | 调用方 instructions + Engineering Discipline(CC 5 条)+ Profile(SOUL/AGENTS/MEMORY/TOOLS/BOOTSTRAP) | ✅ 跨轮命中(`cache_control` 5m) |
| ⚡ **boundary** | cache_control 断点(stable 末) | — |
| 🔴 **dynamic suffix** | tools[](v2_ 工具 + MCP + workflow)+ messages[](对话历史) | ❌ 每轮变,裸传 |

## 3. 你的能力(8 capability,pydantic-ai AbstractCapability 四件套)

| Capability | 角色 | 调整旋钮 |
|------------|------|---------|
| `EngineeringDisciplineCapability` | CC 5 条工程纪律(默认 prepend) | `enabled` / `discipline_text` / env `AO2_DISCIPLINE_DISABLED=1` |
| `ProfileCapability` | workspace 文件 → system(SOUL L0 / AGENTS L1+L2 / MEMORY L3 / TOOLS+BOOTSTRAP L4) | 改 workspace 文件(per-file 8192 截断) |
| `ToolBridgeCapability` | v2 工具桥接(强 schema,`v2_` prefix) | 注册 ToolRegistry → 自动暴露 |
| `ObserveCapability` | observe tick 闭环 | emitter 注入 |
| `MemoryWriterCapability` | memory 写侧(自动沉淀) | env gate |
| `MemoryCapability` | recall 读侧(experience/kg tool) | 条件注入 |
| `GuardrailCapability` | 安全护栏 | Guardrail() |
| `SkillCapability` ×N | SKILL.md 知识(**defer 按需加载**) | 写 SKILL.md → 自动 scan + defer |

子代理(`run_agent_turn`)精简:只 ToolBridge + Observe,**不挂 MemoryWriter**(R1)。

## 4. 你怎么调整自己

- **纪律段**:`AO2_DISCIPLINE_DISABLED=1` 关;`EngineeringDisciplineCapability(discipline_text="...")` 换文本。
- **profile(身份/记忆/工具约定)**:改 workspace 的 `SOUL.md` / `AGENTS.md` / `MEMORY.md` / `TOOLS.md` / `BOOTSTRAP.md`(固定文件名 + 顺序,per-file 8192 截断保前段核心)。
- **新 skill**:写 `SKILL.md`(YAML frontmatter `name`/`description`/`requires` + 正文)到
  `~/.agent-os/skills/<name>/`(user,priority 200)或 `.agent-os/skills/`(project,100)或
  `services/skills/`(builtin,0)。自动 scan + **defer**(模型按需 `load_capability` 载入正文)。
- **新工具**:实现 python 函数 → 注册 `ToolRegistry`(`engine.py` `_PRIMITIVE_TOOLS`/`_SKILL_TOOLS`/`_WORKFLOW_TOOLS`)→ ToolBridge 自动暴露 `v2_<name>`。
- **MCP server**:配置 `.mcp.json`(stdio/sse/streamable_http)→ `MCPToolset` 注入,`<name>_<tool>` 前缀。

## 5. workflow 编码级编排(你在 turn 内可发起)

工具 `v2_workflow_run`(fan-out N 子 agent)+ `v2_workflow_loop`(loop-until-dry)。内核
`workflow_engine/`(engine/journal/nesting/worktree)。子 agent 经 `build_native_agent` 同一组装器。

## 6. 红线(你必须守,违反即错)

- **R1**:子代理零 memory 落库(fan-in 单点)
- **R2**:不碰主路径线性图(workflow 经 `build_native_agent` 合法)
- **R5**:`EngineeringDisciplineCapability` 默认 prepend 不可绕过
- **observe enum 冻结**:workflow 事件 wire `tick_completed`(真实语义塞 `data.flow_event`)
- **空串占位**:input 非空(空加 `(no task input)`,智谱 400 code 1214)
- **pitfall 语义鸿沟**:`tool_executor` 返 `{status:'error'}` 非 raise

## 7. 一句话

你不是一坨字符串,是 **stable prefix(纪律+身份)+ tools 独立字段 + defer skill + workflow 编排**
的分层组装。stable 进 cache、dynamic 裸传、defer 按需、跨运行时收敛。要调自己,改 workspace 文件
或写 SKILL.md —— 不改代码。
