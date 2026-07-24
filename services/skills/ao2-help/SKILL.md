---
name: ao2-help
description: AO2 业务配置详解——面向终端用户。怎么配 agent(agents.yaml)、起服务(start.sh 端口 8001/8002)、看观测(observe)、用 TUI(4 tab)、理解 capability/profile/A2A。新手向导回答业务细节时载入。
requires: {}
---

# AO2 业务配置详解(面向终端用户)

本 skill 是 **AO2 新手向导** 的业务知识库。用户问到下面任何一块且需要细节时,载入本 skill 再答。
面向**终端用户**(用 AO2 干活的人),不是改代码的开发者。所有回答带可操作步骤,不臆造配置。

## 1. agents.yaml — 怎么配 agent

位置:repo 根 `agents.yaml`。结构:

```yaml
defaults:          # 全局默认(缺字段时回退到此)
  model: glm-4.7
  workspace_base: null
  skills: []
agents:            # agent 列表(至少 1 条;只能 1 条 default:true)
  - id: help       # 合法 id:^[a-z0-9][a-z0-9_-]{0,63}$,小写字母数字开头
    default: true  # 唯一一个 default;registry.get(id) 找不到时回退到它
    name: AO2 新手向导
    model: glm-4.7
    workspace: agents/help/workspace   # 身份目录(repo 内 trackable;相对 repo root)
    cwds: []       # 操作 cwd scope;空=单 cwd 退化(workspace 作基准)
    skills: [ao2-help]                  # defer skill 列表
    effort: medium # low/medium/high/xhigh/max
    instructions: |  # 短指令(进 stable prefix,跨轮 cache)
      ...
```

字段速查:

| 字段 | 干啥 | 改它影响 |
|------|------|---------|
| `id` | agent 唯一 id | 路由 key(`/h/<id>`、registry.get) |
| `default` | 是否默认 agent | 路由找不到 id 时回退到它(只能 1 个) |
| `name` | 显示名 | A2A card 投影、TUI 显示 |
| `model` | 智谱模型(glm-4.7 等) | 调用哪个模型 |
| `workspace` | 身份目录(SOUL.md/AGENTS.md 放这) | profile_registry 自动扫此目录 |
| `cwds` | 操作 cwd scope(多服务切换) | agent 能在哪些目录干活 |
| `skills` | defer skill 列表 | 按需载入的业务知识 |
| `instructions` | 短指令 | 进 stable prefix(跨轮 cache 命中) |
| `effort` | 思考强度 | 模型 reasoning 投入 |

**改完不用重启引擎?** 改 `agents.yaml` 后,看 `start.sh` 是不是带 hot-reload;不带就重启对应服务(见第 4 块)。

## 2. capability 体系 — agent 有哪些能力

每个 native agent 由 8 个 capability 组装(改一处全运行时同步,详见 `ao2-architecture` skill):

| Capability | 干啥 | 用户视角 |
|------------|------|---------|
| `ProfileCapability` | 读 workspace 文件 → system prompt | 你改 SOUL.md/AGENTS.md,agent 人设就变 |
| `EngineeringDisciplineCapability` | 5 条工程纪律(默认 prepend) | 别关(红线 R5) |
| `ToolBridgeCapability` | v2 工具桥接 | agent 能调哪些工具 |
| `ObserveCapability` | observe tick 闭环 | agent 行为能被观测 |
| `MemoryWriterCapability` | memory 写侧 | 自动沉淀对话/记忆 |
| `MemoryCapability` | recall 读侧 | 记忆召回 |
| `GuardrailCapability` | 安全护栏 | 防越界 |
| `SkillCapability` | SKILL.md 知识(defer 按需) | 你写的 SKILL.md 自动 scan + defer |
| `CwdScopeCapability` | 多 cwd scope | agent 在多个目录干活 |

## 3. profile 三层 — 怎么调 agent 人设

| 层 | 内容 | 放哪 | cache |
|----|------|------|-------|
| ① `instructions` | 短指令(中文/带步骤/不臆造) | agents.yaml 的 `instructions:` 字段 | 进 stable prefix(跨轮命中) |
| ② profile | 人设 + 规则 | workspace 的 `SOUL.md`(L0)+ `AGENTS.md`(L1+L2) | 进 stable prefix |
| ③ skill | 业务配置详解(defer 按需) | `services/skills/<name>/SKILL.md` | defer,模型按需 `load_capability` 载入 |

**workspace vs cwd scope**:
- `workspace`(身份目录,单):放 SOUL.md/AGENTS.md 等身份文件。profile_registry 自动扫。
- `cwds`(操作目录,多):agent 能在哪些目录干活。空=单 cwd 退化(workspace 作基准)。

## 4. 启动 — 怎么起服务

入口:`start.sh`(repo 根)。常用命令:

| 命令 | 起啥 | 端口 |
|------|------|------|
| `./start.sh dev-orch` | orchestrator(主引擎,FastAPI) | **8001** |
| `./start.sh dev-observe` | observe 观测层 | **8002** |
| `./start.sh dev-tui` | TUI(ratatui,Rust) | 本地终端 |

完整命令清单见 `start.sh` 和 `docs/dev-startup.md`。不确定某个命令干啥,**先读 `start.sh` 源码再答**,别臆造。

## 5. TUI — 4 个 tab

TUI(ratatui)有 4 个 tab:

| Tab | 干啥 |
|-----|------|
| **Control** | 对话输入区(发消息给 agent) |
| **Observe** | 看 agent 行为流(observe 观测层投射) |
| **Flows** | workflow 编排可视化 |
| **Orchestrate** | fork 树 + 人控原语(fork/async/open/cancel) |

## 6. observe — 怎么看 agent 在干嘛

observe 是独立观测层(进程解耦,端口 8002)。agent 每 turn 的 tick 事件闭环到这里。
- TUI 的 **Observe tab** 看实时流。
- observe REST(`http://localhost:8002/...`)程序化拉事件。
- 事件 wire `tick_completed`(真实语义塞 `data.flow_event`)。

"agent 为什么不动了"——先看 Observe tab 或 observe REST 的事件流。

## 7. A2A — agent 之间怎么协作

AO2 的 native/main agent 自成 **A2A 节点**(AgentCard 投影 + catalog + `v2_a2a_call` 工具)。
方向:**对内消费 / 内部 mesh 优先**(native↔main 真 GLM 通话已通)。不是公网 expose。
用户视角:多 agent 能互相调用对方能力,不用自己跑全套 scope。

## 8. 怎么答用户(向导模式)

1. 用户问 X → 先判断 X 属于上面哪块。
2. 带可操作步骤:具体在哪改、敲什么命令、看哪个页面/端口。
3. 不确定具体配置/端口号 → 说"我不确定",指路 `agents.yaml` / `start.sh` / `docs/dev-startup.md`,或建议跑命令确认。**绝不臆造。**
4. 用户要细节且本 skill 没覆盖 → 引导查 `ao2-architecture` skill(self-aware 架构详解,开发者向)。

---

_本 skill = AO2 业务配置详解。L0 人设/L1L2 规则见 help agent workspace 的 SOUL.md/AGENTS.md。self-aware 架构 tune 见 `ao2-architecture` skill(正交,开发者向)。_
