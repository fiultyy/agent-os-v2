---
name: agent-creator
description: 把一个场景描述变成 AO2 agent 的全套配置(agents.yaml 条目 + workspace/SOUL.md + workspace/AGENTS.md + 可选 skill)。Use when 用户描述场景要创建新 agent、配置 spawn agent 行为、或把 agent 行为对齐到特定场景(探索/MVP/debug/release/security/long-term/group-chat)。queen 载入本 skill 跑 Phase A grill + Phase B 投影,最后调 create_agent 工具落盘。
requires: {}
---

# Agent-Creator · 场景化 AO2 agent 生成器

本 skill 是 **queen 创建其他 agent 时的方法论载体**。给定一个场景描述,产出
AO2 全套配置:agents.yaml 条目(结构)+ workspace/SOUL.md(L0 人设)+
workspace/AGENTS.md(L1 身份 + L2 规则) + 可选 skill(defer 按需载入的业务知识)。

## 何时使用

**适用:**
- 用户给一个场景(如「0→1 探索/MVP」「生产 release」「长期共处助理」「安全审计」
  「群聊」),要从无到有创建一个 AO2 agent
- 要把通用 agent 行为聚焦/裁剪到某个工作模式
- spawn 新 agent 前需要先定义它的全套配置

**不适用:**
- 已有 agent 只想做小修小补(直接编辑对应 agents.yaml 条目 + workspace 文件即可)
- 要的是框架/方法论解释文档(本 skill 产物是**可执行的 agent 配置**,不是解释文档)

## 核心方法(一句话)

场景描述 → 逐层 grill 推导行为 profile → 按泛化算法算每条准则权重 → 推出
MUST / MUST NOT / SHOULD → **翻译成纯自然语言** → 落盘 AO2 三件套
(agents.yaml + SOUL.md + AGENTS.md)。

## 两条铁律(违反即失败,先记住)

1. **框架术语零暴露** —— 维度编号、准则编号、profile 数值、权重、阈值,
   只在脑内塑造文本形状,**绝不写进 SOUL.md / AGENTS.md**。一旦写进去,产物
   就从「行为入口指令」退化成「框架解释文档」,agent 会去复述框架而不是按场景行为。
2. **灾难底线不可降权** —— 即便场景明确要激进、要忽略安全,**灾难性不可逆动作**
   (删生产数据 / 泄露敏感 / 未恢复的破坏性改动)恒为 CAN NOT。这是元层唯一
   不让步项,Phase A 显式排除阶段也必须告知用户此项不可关。

---

## 流程总览

```
Phase A — 逐层交互采集(AskUserQuestion,每轮 1–3 问,自然语言选项)
   ↓
Phase B — 投影生成(内部算权重 + 推自然语言约束)
   ↓
落盘 AO2 三件套(调 create_agent 工具)
```

---

## Phase A — 逐层交互采集

用 `AskUserQuestion` 逐层 grill 用户,每轮 1–3 个问题,**选项用自然语言描述**
(绝不暴露维度编号 / 准则编号)。每层采集结果直接映射到内部行为 profile 数值。
用户选「其他」时从自由文本推导。

### A1. 场景定锚

**问题:** 这个 agent 主要做什么?
- 7 预设标签 + 「都不接近,我描述一下」(单选)
- 7 预设:
  - 「写代码 / 本地开发」
  - 「排查 bug / 排查根因」
  - 「调研 / 信息收集 / 跨域类比」
  - 「生产发布 / 上线」
  - 「多人群聊 / 多方协作」
  - 「长期私人助理 / 跨会话共处」
  - 「安全审计 / 高风险高暴露」
- 选择后如有补充描述,记录为微调信号

**映射:** 选择预设 → 直接套用对应基底 profile,后续层叠加微调;选自定义 → 全部从后续层推导。

### A2. 动作环境

- **Q1 改动能轻易回滚吗?** 「本地随便改,git 回退就行」/「部分可逆,push 前要小心」/「几乎不可逆(线上/外发/不可撤回)」
- **Q2 agent 的输出会给谁看?** 「只有我自己」/「团队内部」/「外部用户 / 公开」
- **Q3 出了问题影响多大?** 「只影响当前文件/任务」/「影响整个项目」/「跨系统 / 影响真实用户」

### A3. 自主光谱

- **Q1 你希望 agent 多主动?** 「只回答我问的」/「可以主动补充相关信息」/「能自主决策、事后汇报」
- **Q2 需要多少确认环节?** 「每步都要问我」/「常规操作自动做,关键动作确认」/「全权处理,只看结果」

### A4. 认知风格

- **Q1 对证据和确定性的要求?** 「必须验证过的才说」/「推测可以说,但要标注清楚」/「大胆假设,快速试错」
- **Q2 agent 不确定时该怎么做?** 「直接说不知道,并列出可能性」/「给个最佳猜测,附带置信度」/「挑最可能的走,别废话」
- **Q3 目标达成怎么判断?** 「需要明确的验证标准才算完成」/「大致到位就行,别过度追求」/「方向对就行」

### A5. 社会关系

- **Q1 agent 的社交场景?** 「单 agent 独立工作」/「和多个 agent/人协作」/「群聊环境」
- **Q2 这是长期用还是一次性?** 「一次性任务」/「阶段性项目」/「长期日常共处」
- **Q3 回复风格偏好?** 「少说多做,结果先行」/「适度解释过程」/「详细展开,宁愿多说」

### A6. 工程品味

- **Q1 代码/方案风格偏好?** 「最小可用,能跑就行」/「够用但整洁」/「追求完备和最佳实践」
- **Q2 遇到已有风格不一致的代码?** 「完全匹配现有风格」/「适度改善但不重构」/「按我认为最好的来」

### A7. 显式排除

**问题:** 有哪些行为你想**明确关掉?(多选)
- 「不需要主动建议额外的事」
- 「不需要维护长期关系/用户模型」
- 「不需要社交节制,该说就说」
- 「不需要严格门控,信任 agent 判断」
- 「不需要匹配现有代码风格」
- 「不需要简约,可以冗余」
- 「其他」

**灾难底线(删生产数据 / 泄露敏感 / 破坏性不可逆动作)不可被排除** —— 如果用户
试图排除此项,明确告知「此项不可关闭」。被选中的维度在 profile 中降到仅底线,
对应准则权重大幅压低。

### A8. Profile 确认

**输出关键维度摘要**(挑 4–6 个与预设偏离最大的维度),用自然语言让用户确认:
> "基于你的回答,agent 会被配置为:**高自主**(不需要逐步确认)、**低严谨**
> (推测可以标注后输出)、**短期能回滚**。是否需要调整?"

- 用户确认 → 进入 Phase B
- 用户要求调整 → 回到对应层微调

---

## Phase B — 投影生成(6 步)

> 这 6 步是 agent 内部的推理过程,**不输出给用户**。最终对外产物只有 AO2 三件套。

### B1. 行为 profile 定稿

Phase A 采集的各维度数值即为最终 profile。若有预设基底 + 微调,已叠加完毕。

### B2. 准则权重

对每条行为准则 P,计算权重:`W(P) = Σ_d profile[d] × vector(P)[d]`(或取相关维度 max)。
按 W 排出 **强 / 中 / 弱 / 降权** 四档。

### B3. 元规则调整

- 被 Phase A 显式排除的维度 → 降到「仅底线」
- 不可让步项保留:**灾难底线恒 CAN NOT**(删生产数据 / 泄露敏感 / 未恢复破坏性)
- 多准则冲突 → 按全序裁决:`安全 > 证据 > 范围 > 其它`

### B4. 推理自然语言约束

- 强触发准则 → **MUST**(具象成可执行动作,不写准则编号)
- 硬底线(灾难不可逆 / 认知诚实硬底线)→ **MUST NOT**
- 降权准则 → 放松或反投射(「忽略 X」/「让位给 Y」)
- 场景特征 → **Mission / How / Style**
- 多准则叠加涌现的约束 → 翻译成自然语言并入对应小节

### B5. 落盘标准格式

按下方「AO2 输出模板」生成三件套。**纯自然语言,零框架术语**。

### B6. (可选)合并操作性源

若调用方提供操作性 AGENTS.md(如 pi-default / 现有 workspace/AGENTS.md):
- **客观流程指导**(Commands / Git / PRs workflow)→ **保留**
- **主观行为的客观表述**(Code Quality / Conversational Style / User Override)→
  用场景投影结果**覆盖**

---

## AO2 输出模板(三件套)

create_agent 工具落盘的产物结构:

```
agents.yaml                  # repo 根,append 新条目(结构配置)
agents/<id>/workspace/
  SOUL.md                    # L0 人设骨架
  AGENTS.md                  # L1 身份 + L2 规则骨架
services/skills/<skill_name>/  # (可选,仅当传 skill_name + skill_md)
  SKILL.md
```

### 模板 1:agents.yaml 条目骨架(append 进现有 agents.yaml)

```yaml
- id: <agent-id>                 # 合法 id: ^[a-z0-9][a-z0-9_-]{0,63}$
  default: false                 # 通常 false(已有 default 不覆盖)
  name: <显示名>                 # TUI / A2A card 投影用
  model: <模型>                  # 缺则回退 defaults.model
  workspace: <绝对路径>          # repo 内 trackable 目录;必须绝对路径
                                 # (orchestrator 启动 cwd=services/orchestrator,
                                 # 相对路径会拼错)
  cwds:                          # 操作 cwd scope;空 → 单 workspace cwd
    - path: <相对 repo root>
      label: <可选句柄>
      default: <true|false>      # 最多 1 个 default=true
  skills: [<skill-name>, ...]    # defer skill 列表(按需 load_capability)
  effort: medium                 # low/medium/high/xhigh/max
  instructions: |                # 短指令(进 stable prefix,跨轮 cache)
    <2-4 句核心定位,中文,带可操作要点>
```

### 模板 2:workspace/SOUL.md(L0 人设骨架)

```markdown
# <Agent 显示名>

> <!-- 一句话:这个 agent 是谁、为谁服务、什么气质。
>    从 Phase A 场景 + 认知风格推导。不是规则,是「它的灵魂」。
>    例如:「一个急性子但严谨的探索者,容忍推测但必须标注证据。」 -->

## Persona

<!-- 2–4 句:性格底色。怎么看待自己的工作、怎么对待用户、什么会让它兴奋/不耐烦。
     这是 profile 在人层面的投影,不写规则(MUST/MUST NOT 在 AGENTS.md 里)。 -->
```

### 模板 3:workspace/AGENTS.md(L1 身份 + L2 规则骨架)

> 对齐 agents-md-generator assets/AGENTS-template.md。L1 身份 + L2 规则,
> profile_registry 自动 split。

```markdown
# AGENTS.md

> <!-- 一句话定位:什么场景 + 这是什么类型 agent + 覆盖说明
>    (如:操作性指导见外部链接,本文件只管行为) -->

---

## Agent Behavior

<!-- 2–4 句:这个 agent 的价值主张与角色。不是按部就班执行规范,而是… -->

### Mission

<!-- 按顺序的阶段目标;每个够用就进下一个,不要卡在前面"做到完美" -->
1. **阶段一** — …
2. **阶段二** — …

### How you work

<!-- 工作方式要点 -->
- …

### MUST

<!-- 强触发准则 → 具象成可执行动作。不写准则编号 / 向量 / 算法 -->
- …

### MUST NOT

<!-- 硬底线 + 场景反投射。灾难性不可逆动作恒在此 -->
- 灾难底线:删生产数据 / 泄露敏感 / 未恢复的破坏性改动 —— 恒禁止
- …

### Style

<!-- 沟通/思维风格,含场景显式忽略项 -->
- …
- 忽略 …(除灾难底线)

### Output

<!-- 各阶段交付物形态 -->
- **阶段一**:…
- **阶段二**:…

---
```

---

## 错例对照(避免重犯)

| 错(暴露框架) | 对(自然语言) |
|---|---|
| 「P1 强触发,w=0.9」 | 「每个结论标注证据强度(已证/推测/类比)」 |
| 「19 维 profile:D6=0.9」 | 「主动跨领域类比、外推关联」 |
| 「按泛化算法 W≥0.70 出 MUST」 | (直接写出 MUST 动作,不提算法) |
| 把 AGENTS.md 当框架演示文档 | 当 agent 入口指令——读它就知道怎么行为 |
| 准则/操作混着保留 | 主观行为表述覆盖、客观流程指导保留 |
| workspace 用相对路径 | workspace 用绝对路径(orchestrator cwd=services/orchestrator) |

---

## 交付

只产出 AO2 三件套(agents.yaml 条目 + workspace/SOUL.md + workspace/AGENTS.md,
可选 skill),不附向量推理 / profile 表 / 权重计算。最后调 `create_agent` 工具
原子落盘 + 提示用户「重启 orche 生效」(registry 启动加载,不做热加载——YAGNI)。

产物读起来像 agent 入口指令,不是框架解释。
