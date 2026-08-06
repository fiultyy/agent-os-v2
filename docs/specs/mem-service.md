# Spec: mem-service

Date: 2026-08-06
Status: Draft
ADR: docs/adr/mem-service.md
Iteration Base: 63e2049f81cdcb0efaa33a7a6c1a2cc92a8ea6e0
QA Available: false(记忆服务非 web app,无 QA intent 目标;全 general_test)

## 1. Problem Statement
CC 现有记忆(~/.claude memory md,即时派热/温层)缺结构化 fact 层、KG 召回;长任务/开放探索下 index 膨胀、散 fact 进不了 context、无关系/时序/矛盾消解。需一个独立记忆服务承载 KG fact 层 + 召回,叠加在 CC 现有记忆之上(不替换),供 CC/AO2/多 agent 复用。(type-aware 衰减 v1 defer,随 autoDream consolidate 阶)

## 2. Solution (In-Scope)
- Python 独立服务 `services/memory-service/`,cli 子命令 ingest/recall/consolidate(无 query,调试用 `recall --verbose`)
- SQLite + networkx 存储,KG schema(Entity + Fact reified + 正交元数据;**无 MemoryItem 表**,Fact reification 自包含)
- 召回借鉴 AO2 scored=match×lif(match_item 抄 weighted_recall.py:54-88,**lif 读 Fact.LIF 标量非 NeuralField**),KG 导航定位 Fact
- 正则 EntityExtractor 确定性抽取(7 英文谓词+中文同义集+9 模式类,无 LLM)
- CC skill **源在仓内 `services/memory-service/skill/`**(deploy 独立步骤 P4),包装 cli 供 CC 按需调用
- 闭环可测: ingest→recall→命中
- **v1 CC 集成形态: 用户手动 `/mem` 或 CC 读 SKILL.md 按需调 cli,无三频 hook 自动注入**(per-turn 连续性 defer)

## 3. Out-of-Scope (Non-Goals)
- 三频 hook(UserPromptSubmit 增量条/PreCompact 收敛)— defer
- 向量实体tag 子图入口 + 聚合度重排 — P4 defer
- 冷层类聚 / autoDream 后台巩固 — defer
- type-aware 衰减(需 per-type half_life + 触发器)— defer(随 autoDream consolidate 阶)
- LLM 抽取/蝴蝶翼多路 — defer
- query 独立 cli(调试用 `recall --verbose` 或 sqlite3 直查)— defer
- 改 CC ~/.claude/projects/*/memory/*.md — 永不(叠加非替换)
- Web UI / 远程多机 — v1 单机 cli

## 4. User Stories / Scenarios
1. As CC agent, I want `mem recall "<query>"` 拿相关 Fact, so that 对话可注入结构化记忆
2. As CC agent, I want `mem ingest "<text>"` 把对话/事实落 KG fact, so that 记忆持久化
3. As developer, I want `recall --verbose` 看命中明细(entity/fact/scored), so that 调试记忆库(query 独立 cli defer)
4. As developer, I want `mem consolidate` 触发去重, so that 记忆库治理(v1 去重骨架,衰减 defer)
5. As CC, I want skill 包装 cli **供按需调用**(去手动直输 cli;非 per-turn 自动注入), so that 接入方便
6. GIVEN ingest "用户使用 rust 进行开发" WHEN recall "rust" THEN 返 Fact(subject=用户,predicate=uses,object=rust), scored=match×lif(字面命中;中文同义/省称 defer 到向量层)

## 5. Implementation Decisions
- 服务形态: 独立 Python 进程 + cli + skill 接入 [ADR-1]
- 存储: SQLite + networkx,Entity+Fact 表(无 MemoryItem) [ADR-2]
- KG schema: Fact reification + 正交元数据(升级 AO2 Relation 边属性)[ADR-3]
- 召回: scored=match×lif(match_item 抄 :54-88, lif 读 Fact.LIF 标量) [ADR-4]
- 抽取: 正则 EntityExtractor(7 英文+中文同义+9 模式,无 LLM) [ADR-5]
- 范围: v1=P3 级,衰减/query/三频hook/向量/冷层/autoDream defer [ADR-6]
- 不改 CC 记忆: 叠加路线,skill 源仓内+deploy 独立 [ADR-7]

## 6. Testing Decisions
- Seams: cli 子命令(最高 seam,1 个)— 通过 cli stdin/stdout 测
- 测试模块: ingest/recall/consolidate 的 cli 行为(无 query)
- Prior art: AO2 weighted_recall.py match_item 测试模式(:54-88 零 LLM 可单测;types/neural_field 依赖需重写不照搬)

## 7. Acceptance(关联编排图节点)
- [ ] Node A: SQLite schema(Entity+Fact reified,无 MemoryItem)+ store CRUD → general_test
- [ ] Node B: ingest cli 抽 entity/fact 入库(正则,必出实体输入) → general_test
- [ ] Node C: recall cli KG 导航返 Fact + match×lif 排序 → general_test
- [ ] Node D: consolidate cli 去重骨架(无衰减) → general_test
- [ ] Node E: CC skill 源(仓内)包装 cli → general_test
- [ ] Node F: ingest→recall 闭环命中+排序+schema 跨表 join 一致 → general_test
- [ ] P3 Regression: 全节点闭环复跑(general_test)
- [ ] ADR Compliance: 7 条全 upheld

## 8. Open Issues
(空 — 3 轮 grill 已修正 P0+P1,范围/协作/存储/AO2 借鉴全对齐)

## 9. Defer 预判
- 三频 hook(UserPromptSubmit/PreCompact,需启用 CC 禁用 hook + 改 settings)(后续阶)
- 向量实体tag + 聚合度重排(需 embedding 模型/向量存储)(P4)
- 冷层类聚 / autoDream daemon(需调度)(后续)
- type-aware 衰减(需 per-type half_life + consolidate 触发设计)(随 autoDream consolidate 阶)
- LLM 抽取/蝴蝶翼(需模型依赖)(后续)
- query 独立 cli(调试用 `recall --verbose` 或 `sqlite3 services/memory-service/data/*.db` 直查)
- skill deploy: 仓内源 `services/memory-service/SKILL.md`(服务根, 与 cli.py 同级) → `~/.claude/skills/mem/`(软链**服务根** `~/.claude/skills/mem → services/memory-service/`, cli.py+SKILL.md 全入; 非 skill/ 子目录)。**注: SKILL.md 在仓内服务根(CC 发现 ~/.claude/skills/mem/SKILL.md + cli 同目录裸 import work); v1 已 deploy 软链; ops grill 抓 P0 修(SKILL.md 从 skill/ 移根 + 软链服务根, 解 L45 deploy 路径失效)**
- **v1 召回=子串/前缀匹配(match×lif on Fact + KG entity.name LIKE),中文同义/省称/改写 query 命中率低;语义召回 defer 到向量实体tag+聚合度重排层(P4)**
- skill 把服务 fact 投影回 CC md(v1 不做,桥接留后)
