# Agent OS 100% 完成迭代计划

> ⚠️ **本文已失效**(2026-06-27):"98% 完成"声明经代码全景对抗 verify 证实严重失实 —— 大量标 ✅ 的 D-* 实为"完整实现却零接线"的孤岛(如 D-21 control / D-19 CAContextCoding / D-15 蝴蝶翼),且完全漏记致命断裂(gateway→orchestrator 全量 `/v1` 缺失致生产全 404、前端 Canvas WS 三断、L3 工具链悬空、CommunicationBus 空壳)。**已被 [`docs/mvp-iteration-roadmap.md`](mvp-iteration-roadmap.md) 取代**。本文保留作历史记录。

> 基于 architecture.md + 代码审查（2026-04-21）
> **当前完成度(❌ 失实声明,见顶部红标)**: 原文 "98%(D-03~D-29 + P4 recall + API /v1/ + 记忆迭代 P0-P3,2026-06-20)" —— 经 2026-06-27 代码全景对抗 verify 证实**严重失实**(大量标 ✅ 的 D-* 为"完整实现却零接线"孤岛),实际进度以 [`docs/mvp-iteration-roadmap.md`](mvp-iteration-roadmap.md) §4 为准。
> **目标**: ~~100%~~ 已重定向为 MVP 北极星(端到端可跑通,见 mvp-iteration-roadmap.md §1)

---

## ⚠️ 重要：当前差距（2026-04-21 审查）

### 已实现（✅ 16个）
D-03, D-04, D-05, D-08, D-09, D-11, D-13, D-14, D-15, D-19, D-20, D-22, D-23, D-24, D-26, D-27, D-28, D-29

### 部分实现（⚠️ 4个，需补全）
| D-* | 状态 | 缺什么 |
|------|------|--------|
| D-16 | 2/6 | ConditionalSpawner + Sandbox |
| D-17 | 4/10 | Kairos + Dreamer + Critic |
| D-21 | ✅ 全部 | InterceptLayer + ReasoningLayer |
| D-25 | ✅ 全部 | BackwardWriter 写回工具 |

### 未实现（❌ 1个）
| D-* | 说明 |
|------|------|
| D-14 | ~~未实现~~ ✅ 已完成（L0-L5 Layer Stack）|

---

## 迭代步骤（按顺序）

### 迭代 1：D-14 Agent Base Profile（L0-L5 Layer Stack）

**目标**：建立 Agent 的分层配置体系，System Prompt 动态化

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 1.1 | 设计 L0-L5 Layer Stack 数据模型 | `src/agent/profile.py` |
| 1.2 | 实现 ProfilePlugin 可插拔机制 | `src/agent/profile_plugins.py` |
| 1.3 | SOUL/AGENTS 文件解析 | 解析 SOUL.md → L0，AGENTS.md → L1+L2 |
| 1.4 | Profile 模板系统 | 内置 coding/research/general 模板 |
| 1.5 | 与 ContextCompiler 集成 | Profile 内容影响系统 prompt 组装 |

**验收**：
- [ ] AgentBaseProfile 包含 5 个 Layer，可序列化
- [ ] 不同 Profile 编译出的 System Prompt 不同
- [ ] Profile 可运行时切换

---

### 迭代 2：D-20 PitFail 踩坑档案

**目标**：建立项目级踩坑记录和召回系统

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 2.1 | Pitfall 数据模型 | `src/pitfail/models.py` |
| 2.2 | PitfailRegistry SQLite CRUD | `src/pitfail/registry.py` |
| 2.3 | 自动匹配拦截器 | 工具执行失败时自动匹配历史 |
| 2.4 | LLM 摘要生成 | 新记录时调用 LLM 生成摘要 |
| 2.5 | 与 Guardrail 集成 | 注入历史解决方案 |

**验收**：
- [ ] POST /api/v1/pitfalls 可创建记录
- [ ] GET /api/v1/pitfalls/search 可搜索匹配
- [ ] 工具错误自动触发匹配

---

### 迭代 3：D-15 蝴蝶模型（18项特性）

**目标**：实现蝴蝶翼双向联想的记忆激活机制

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 3.1 | 蝴蝶翼数据模型 | `src/memory/butterfly_wing.py` |
| 3.2 | 正向翼（归纳翼）| forward_metadata + score |
| 3.3 | 反向翼（锚定翼）| backward_metadata + score |
| 3.4 | 双翼激活触发器 | composite_threshold 协调 |
| 3.5 | 蝴蝶翼 UI 数据接口 | /wings 返回双翼详情 |
| 3.6 | 集成到 Semantic Memory | L2→L3 迁移时自动计算 |

**18项特性清单**：F1-F12（6归纳翼+6锚定翼）+ B1-B6（双翼协调）

**验收**：
- [ ] /wings 返回 {forward: [...], backward: [...]}
- [ ] 18项特性全部通过单元测试

---

### 迭代 4：D-19 CA Coding 场景特化

**目标**：为 Coding Agent 场景特化 Context Architecture 六层

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 4.1 | CA 六层数据模型 | `src/context/coding_context.py` |
| 4.2 | CodeAgentProfile | 注入 coding 专用 SOUL/AGENTS |
| 4.3 | ToolContextInjector | 工具调用时注入相关 API 文档 |
| 4.4 | CodebaseContextBuilder | 构建相关文件列表 + 摘要 |
| 4.5 | GitContextProvider | 自动注入 git diff/branch/log |
| 4.6 | CodeReviewer 集成 | 调用 skills/code review 能力 |

**验收**：
- [ ] POST /api/v1/coding/context 生成完整 CA 六层上下文
- [ ] Coding 场景包含代码库摘要 + Git diff + API 文档

---

### 迭代 5：D-13 L3.4 Tool Catalog

**目标**：完成工具分层体系最后缺失的 L3.4 Catalog

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 5.1 | ToolCatalog 类 | `src/tools/catalog.py` |
| 5.2 | Catalog API 端点 | GET /api/v1/tools/catalog |
| 5.3 | 工具发现机制 | 根据任务类型推荐工具层 |
| 5.4 | 工具版本管理 | 多版本注册，热回滚 |
| 5.5 | 与 ToolRegistry 集成 | 查询优先走 Catalog |

**验收**：
- [ ] GET /api/v1/tools/catalog 返回三层分类列表
- [ ] 支持按 layer/tags 过滤

---

### 迭代 6：D-16 Meta Agent 补全

**目标**：完成条件型任务生成和隔离执行机制

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 6.1 | ConditionalSpawner | cron/event/queue 三种触发 |
| 6.2 | SandboxExecutor | 隔离进程执行，资源限制 |
| 6.3 | SubagentLifecycleManager | 父子 Agent 生命周期管理 |
| 6.4 | MetaAgentAPI | POST /api/v1/meta/spawn |
| 6.5 | MetaAgentNode | 与 Graph State Machine 集成 |

**验收**：
- [ ] POST /api/v1/meta/spawn 可创建子 Agent
- [ ] Sandbox 执行超时自动终止

---

### 迭代 7：D-17 Side Agent 补全

**目标**：完成 Kairos/Dreamer/Critic 三个角色

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 7.1 | KairosAgent | LIF 电位触发时机注入 |
| 7.2 | DreamerAgent | 定时将 session 巩固到 episodic |
| 7.3 | CriticAgent | 置信度触发事实纠正 |
| 7.4 | SideAgentBus | 慢/中/快三通道注入 |
| 7.5 | 集成到 Orchestrator | Side Agent 以独立线程运行 |

**验收**：
- [ ] Kairos 在 turn_count 达标时注入时机建议
- [ ] Dreamer 每小时巩固一次
- [ ] Critic 在置信度 < 0.5 时注入纠正

---

### 迭代 8：D-21 三层控制补全

**目标**：完成拦截层和推理层

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 8.1 | InterceptLayer | 请求/响应拦截，阻断/修改/放行 |
| 8.2 | ReasoningLayer | Chain-of-Thought 追踪 |
| 8.3 | ControlPlaneAPI | POST /api/v1/control/intercept |
| 8.4 | 与 Guardrail 集成 | 阻断信息可追溯 |
| 8.5 | 推理可视化接口 | 返回推理路径图 |

**验收**：
- [ ] POST /api/v1/control/intercept 可配置拦截规则
- [ ] GET /api/v1/control/reasoning/{session_id} 返回推理路径

---

### 迭代 9：D-25 Backward 写回工具

**目标**：完成 Backward Memory 写回机制

| 子任务 | 内容 | 关键文件 |
|--------|------|---------|
| 9.1 | BackwardWriter | 将判断结果写回 Main Agent |
| 9.2 | 三通道写回 | 慢（LLM摘要）/ 中（关键词）/ 快（结构化指令）|
| 9.3 | 写回优先级调度 | 根据 confidence 决定通道 |
| 9.4 | 与 SideAgentBus 集成 | 通过 Bus 注入 |
| 9.5 | 写回效果验证 API | GET /api/v1/memory/backward/log |

**验收**：
- [ ] BackwardWriter.write() 可写回 Main Agent
- [ ] confidence > 0.8 用快通道，< 0.5 用慢通道

---

## 最终验收清单

| 迭代 | 文件 | 验收项数 |
|------|------|---------|
| 1 | profile.py + profile_plugins.py | 4 |
| 2 | pitfail/registry.py + matcher.py | 4 |
| 3 | butterfly_wing.py | 4 + 18 |
| 4 | coding_context.py + task_decomposer.py | 4 + 6 |
| 5 | tools/catalog.py | 4 |
| 6 | agent/meta/*.py | 4 |
| 7 | memory/sideline/kairos.py + dreamer.py + critic.py | 4 |
| 8 | control/intercept_layer.py + reasoning_layer.py | 4 |
| 9 | memory/sideline/backward_writer.py | 4 |

### 新增迭代（2026-04-21 Meta-Harness 讨论）

| 迭代 | 文件 | 验收项数 | 状态 |
|------|------|---------|------|
| A | kg_query_interface.py + kg_memory_tool.py | 5 | ✅ 完成 |
| B | sideline/transcriber.py | 4 | ✅ 完成 |
| C | sideline/reuse_tracker.py | 4 | ✅ 完成 |
| D | experience_kg.py + tools/experience_tool.py | 6 | ✅ 完成 |

### D-30 Sideline Agent Prompt System（v2 — Committee + Butterfly Signal，2026-04-22）

**设计文档**: `docs/D-30-sideline-agent-prompt-system.md`

**核心架构**:
- **ScoringPolicy 独立抽象** — pluggable strategies，方便迭代
- **Butterfly Signal 驱动进化** — forward × backward wing × co_occurrence
- **Profile Generation Committee** — Transcriber/Refiner/Architect 三角色协商
- **LLM 角色是"解释者"** — 不做决策，只生成 human-readable description

**新增模块**:
```
ScoringPolicy Protocol  ← 独立抽象，可插拔
    ├── ButterflySignalPolicy  (+ KG 图结构特征)
    └── ReuseThresholdPolicy
ScoringEngine            ← 多策略组合（weighted/max/min/any）
ProfileGenerationCommittee  ← 三角色 JSON 协商
ScoringCalibrationSystem   ← 支线系统，阈值 F1 自收敛
    └── BundleHistory        ← bundle 调用追踪，ground truth
```

**实施状态**:
- [x] D-30 v2 设计完成
- [ ] Phase 1: Scoring 抽象层（ScoringPolicy + ScoringEngine + 2 策略 + KG 图结构）
- [ ] Phase 2: Committee 框架（Committee + TaskSpec + BundleHistory）
- [ ] Phase 3: Transcriber 改造
- [ ] Phase 4: Refiner + Architect 实现
- [ ] Phase 5: 端到端集成 + CalibrationSystem 自收敛

---

### 迭代 10：改造点对齐（2026-06 记忆迭代）

**设计文档**: `docs/memory-iteration-plan.md`(P0–P4,6 阶段)

**背景**: 基于记忆系统外部研究(第七、八章 hermes 对标),对 agent-os-v2 记忆子系统做 P0–P3 代码改造 + P4 文档对齐。

**子任务**:
| 子任务 | 内容 | 状态 |
|--------|------|------|
| 10.1 | agentskills.io skill bundle 标准对齐(项目暂无 skill,文档化标准) | ✅ P4 |
| 10.2 | FAISS 真相校准(`vector_store=None`,KG 为主;代码保留为 pgvector 预留) | ✅ P3 |
| 10.3 | architecture-comparison 补齐主流记忆系统对比(hermes/Letta/Mem0/Zep) | ✅ P4 |
| 10.4 | P0 origin provenance 产权边界(MemoryOrigin,堵 DreamerAgent 乱改) | ✅ feat/memory-provenance |
| 10.5 | P1 事件总线(chat.py 11 处调用解耦 → MemoryEventBus) | ✅ feat/memory-event-bus |
| 10.6 | P2 Anthropic cache 友好化(compiler 三层 + llm_client 双通道) | ✅ feat/memory-cache |
| 10.7 | P3 状态机 + 在线巩固(MemoryState 三态 + TaskConsolidator) | ✅ feat/memory-state-machine |

**验收**:
- [x] tech-debt.md 含 TD-007/008/009 且标 P2
- [x] 四优势(蝴蝶翼/信任域/五维评分/三模式召回)零触碰(P0–P3 红线)
- [x] FAISS 真相校准:代码保留但运行时未启用,KG 为主
- [x] architecture-comparison 补齐主流记忆系统对比
- [x] P0–P3 单元 + e2e 验证全过(60 测试 + cache 命中 + TaskConsolidator 触发)

**遗留**: Phase 6 交叉验收(全链路 origin 一致 + 四优势回归 + migration 双向)。

---

*文档更新: 2026-06-20*
*状态: 98% 完成（D-03~D-29 + P4 recall + API /v1/ + 记忆迭代 P0-P3）*
*新增: D-30 Sideline Agent Prompt System;迭代 10 改造点对齐*
