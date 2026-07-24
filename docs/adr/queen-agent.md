# ADR: queen-agent
Date: 2026-07-24
Status: Active
Iteration base: 9214f68

## ADR-1: queen = agents-md-generator 方法论 + AO2 架构
Status: Accepted
Context: `agents-md-generator` skill 有系统的 profile 创建方法论（场景→19 维 trait→准则权重→MUST/MUST NOT/SHOULD→纯自然语言 AGENTS.md，Phase A 交互采集 + Phase B 投影），但输出是单 AGENTS.md，不符合 AO2 多文件 agent 结构。
Decision: queen agent 继承 agents-md-generator 核心思想 + 了解 AO2 架构，用于创建其他 agent。映射：agents-md-generator 的 AGENTS.md（定位+Behavior→L1 身份 / Mission·How·MUST·MUST NOT·Style·Output→L2 规则）直接落 AO2 `workspace/AGENTS.md`（profile_registry 自动 split L1+L2）。queen 相对 agents-md-generator 的增量 = 补 `SOUL.md`（L0 人设，从场景推导 persona）+ 生成 `agents.yaml` 条目（结构配置）+ 可选 skill。
Consequences: queen 工作流 = 用户描述场景 → Phase A grill（8 层）→ Phase B 投影生成 AGENTS.md → 补 SOUL.md → 生成 agents.yaml 条目 → create_agent 工具落盘。
Constrains: [A.T1, B.T1, B.T2]

## ADR-2: 方法论挂 agent-creator skill（defer 按需载入）
Status: Accepted
Context: agents-md-generator 的 19 维方法论 + 投影 SOP 是重型知识（references/projection-sop.md + behavior-space-core.md + scenario-profiles.md），常驻 queen system prompt token 高。
Decision: 把方法论 + AO2 输出模板（agents.yaml 条目骨架 + SOUL.md 骨架 + AGENTS.md 骨架）封装成 `services/skills/agent-creator/SKILL.md`（defer skill），queen `skills: [agent-creator]`，按需 `load_capability` 载入。符合 AO2 范式（help 挂 ao2-help 同款），方法论不常驻保 cache prefix 稳定。
Constrains: [A.T1, B.T1]

## ADR-3: create_agent 工具（queen 写文件能力）
Status: Accepted
Context: tool_executor 无 write_file/create_agent 工具（src/tools/ 仅 catalog/registry/executor/cwd_scope/guardrail）。queen 要"创建 agent"必须有写文件能力。
Decision: 新建 `create_agent` 工具（注册到 tool_executor）。参数 = agent 配置（id/name/model/workspace/skills/instructions/cwds + soul_md 内容 + agents_md 内容 + 可选 skill）。行为：① normalize_agent_id 校验 + 唯一性 ② append agents.yaml（保留 defaults + 现有 agents，原子写）③ mkdir workspace + write SOUL.md/AGENTS.md ④（可选）创建 skill 目录 ⑤ 返回成功 + 提示重启 orche 生效（registry 启动加载，不做热加载——YAGNI）。queen `tools: {allow: [create_agent]}`（白名单限工具）。
Consequences: agents.yaml 是关键配置,工具必须校验 id 合法 + 唯一 + schema(AgentsConfig.model_validate) + 原子写(写临时文件 + rename),写错不破坏现有 agent。灾难底线:不覆盖现有 agent(同名拒)。
Constrains: [A.T2, B.T1, B.T3]
