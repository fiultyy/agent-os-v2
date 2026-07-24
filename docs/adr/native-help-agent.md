# ADR: native-help-agent
Date: 2026-07-24
Status: Active
Iteration base: edd75d0

## ADR-1: 接线 AgentSpec.instructions（修复 dead 字段）
Status: Accepted
Context: `AgentSpec.instructions`（79e9e92 persistence 引入）从未被 `_build_native_session` 消费（routes.py pickaxe `spec.instructions` 空），是 dead 字段。`instructions` 是 pydantic-ai `Agent.__init__` 原生参数（非 AO2 自造）。
Decision: `_build_native_session`（routes.py:473）调 `build_native_agent` 时传 `instructions=spec.instructions or ""`。注入位置 = base instructions 段（`Agent(instructions=...)`）—— pydantic-ai 2.0 `_get_instructions`（agent/__init__.py:2478-2479）把 base 放最前、cap（Profile/Engineering/Skill）在后；base + 静态 cap 被 `'\n'.join` 合并成单个 `InstructionPart(dynamic=False)`（agent/__init__.py:1395），被现有 `anthropic_cache_instructions="5m"` 整段缓存。
Consequences:
  - **cache 隐性耦合**：instructions 必须永远静态字符串。改 `dynamic=True` 会连带 cap 段翻 dynamic 丢 cache（传染性，engineering_discipline docstring 已标）。`native_agent.py` docstring 补标此约束。
  - **来源 = agents.yaml 字段**（不读文件），避免与 profile（SOUL.md/AGENTS.md）重复。高频变动内容走 defer skill，不走 instructions。
  - 接线成本一次性：该 agent 首 turn 整段 cache miss（内容从空变实），之后稳定。help agent 全新建无现成 cache 可破坏。
Constrains: [A.T1, A.T2]

## ADR-2: help agent 三层 prompt 分工
Status: Accepted
Context: 移除两个预设 agent（`native`"harness 迭代专员"面向开发者 / `main`"全能助理"claw 迁移），换成面向终端用户的 AO2 新手向导。路由 registry-driven（`registry.get(agent_id) or default()`），换 default agent 路由自动跟上。
Decision: help agent（`default: true`）三层 prompt 分工：① `instructions`（agents.yaml 短指令：中文/带步骤/不臆造配置）② profile（workspace `SOUL.md` 人设 + `AGENTS.md` 规则）③ skill（`ao2-help` SKILL.md 业务配置详解，defer 按需）。`workspace: /home/yy/projects/agent-os-v2/agents/help/workspace`（**绝对路径**——`agent_registry.resolve_workspace` 用 `Path(workspace).resolve()` 按 cwd 解析，而 start.sh 的 orchestrator 子 shell `cd services/orchestrator` 会使相对路径拼成 `services/orchestrator/agents/help/workspace` 不存在 → profile 静默 skip；与 native/main 一致用绝对路径。P3 实测确认），`cwds: []`（面向用户无多服务 cwd 需求，参照 main 退化）。
Alternatives: 只用 profile（token 高）/ 只用 skill（无人设）/ instructions 读文件（与 profile 重复）。
Constrains: [B.T1, B.T2, B.T3]

## ADR-3: TUI new ao2 session 列出 agent 选择
Status: Accepted
Context: TUI new ao2 session 不列 registry 的 agent（`main.rs` agent_id 硬编），用户无法选。后端只有 `/claw/agents`，缺 agent-os-v2 的 list-agents。
Decision: 后端加 `GET /h/agent-os-v2/agents`（返 `registry._agents` 的 `{id, name, default}`，复用 registry 已有数据）。TUI new-session 流程拉该端点列出 agent，用户选（默认 default）→ `POST /h/agent-os-v2/sessions` 带 `agent_id`。**契约固定** `{agents: [{id, name, default}]}` 让后端(T1)与前端(T2)并行。
Constrains: [C.T1, C.T2]

## 红线（继承）
- R1: 不碰 workflow_engine.py / Flows（workflow 冻结红线）
- R5: services/observe 无 memory_event_bus/_trigger_ingest/memory_service
- RK11: 工具注册名源码无 v2_ 前缀
