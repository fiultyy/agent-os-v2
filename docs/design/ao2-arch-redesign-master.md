# AO2 架构清理 Master Plan(轻清理,非重构)

Date: 2026-07-25(第三轮读代码后回退重写)
Status: Master Design(轻清理,待 plan)
子文档:[轴 1](axis-1-tool-relayer.md) · [轴 2](axis-2-hooks.md) · [轴 3](axis-3-acp-channel.md)

## 1. 北极星(修正)

AO2 当前抽象层**主体合理**(经读代码验证),北极星从"重构三层地基"修正为"**清理冗余 + 补缺失 hooks + 验证通道需求**":
- **删死代码 + 命名归一**(轴 1):ToolLayer 死元数据 / 死包 / 双写
- **补 hooks event**(轴 2):泛化现有 event bus,补 Tool/Stop/Submit/Subagent
- **验证 ACP 必要性**(轴 3):先验 a2a 是否够用,不预设建 ACP

**不做四层重构**(曾设计的 kernel/tools/binding/skill 基于"ToolLayer 伤害运行时"错误前提,读代码证伪,见 §7)。

## 2. 三轴总览(降级版)
| 轴 | 曾设计 | 修正后 | 依据 |
|---|---|---|---|
| 1 工具 | 四层正交重构 | **删死代码 + ToolLayer 决策** | ToolLayer 零运行时消费(catalog 零调用) |
| 2 hooks | 从零建 HookRegistry | **泛化现有 bus + 补 event** | memory event bus 已是雏形(多消费者注册) |
| 3 ACP | 判据:驱动 vs 内容声明式 | **defer,先验 a2a**;启动则 A2A over ACP | fan-out 硬编码驱动**不走** ACP,A2A 内容声明式**走** ACP |

## 3. 路线图(轻量阶段)
```
阶段 1 清理(轴 1)  ──→  阶段 2 补 hooks(轴 2)  ──→  阶段 3 验证(轴 3,条件)
删死代码 + ToolLayer     泛化 bus + 补 Tool/Stop       真机测 a2a 瓶颈
+ 命名归一(可选)         + guardrail 注册式            痛点成立才 ACP
```
- **阶段 1(1-2 轮)**:C1 删 skill_catalog/control + C2 ToolLayer 决策(推荐删 catalog)+ C4 单写
- **阶段 2(2-3 轮)**:H1 bus EventType 扩展 + H2 fire 点 + H3 guardrail 注册式
- **阶段 3(条件)**:P0 验证 a2a → 痛点成立才 ACP/session 管理层

## 4. 依赖图
```
轴 2 event bus(泛化)— Tool fire 点接入
  └── 轴 1 ToolExecutor(Pre/Post 替代硬编码 guardrail)— 依赖 bus 扩展
轴 3 ACP(条件)— 依赖 a2a 瓶颈验证
```
- 阶段 1(轴 1 删死代码)与阶段 2(轴 2 bus)可并行(删 catalog 不依赖 bus)
- 阶段 3(轴 3)独立,条件启动

## 5. 全局边界
- **不重构四层**(回退,见 §7)
- 不改 claw ws(:18789 不动)
- 不动 workflow_engine.py/flow.py(红线 R1)
- 不碰 observe memory_event_bus/_trigger_ingest/memory_service(R5)
- 工具注册名源码无 v2_ 前缀(RK11)

## 6. 验收
- [ ] 死包删(skill_catalog/control)+ ToolLayer 决策落地
- [ ] bus EventType 覆盖 Tool/Stop/Submit/Subagent + guardrail 注册式
- [ ] a2a 瓶颈验证有结论(够用 defer / 不够启动 ACP)
- [ ] 全程:guardrail/memory/a2a/workflow/18 工具行为回归不变

## 7. 回退记录(重要留痕)

### 7.1 曾设计(2026-07-25 第一/二轮 grill)
三轴大重构:
- 轴 1:基建层 kernel / tools / binding / skill 四层正交 + ToolLayer 大爆炸废弃 + agents.yaml 破坏性迁移 + 原语细粒度
- 轴 2:从零建 HookRegistry 对标 Claude 27 全 fire + 消费者全迁移
- 轴 3:ACP 全套(protocol + native + 三方 adapter + streaming + 双向)
- 10 条决策 + 4 条 v2 细化(skill fusion / session 管理层 / ACP vs spawn / ws adapter)

### 7.2 证伪(第三轮读代码)
1. **ToolLayer 零运行时消费**:`list_by_layer`/`filter_dict`/`list_by_category`/`list_by_tags` grep 全空调用 → catalog 死元数据 → "ToolLayer 混维度伤害运行时"前提不成立 → 四层重构 over-engineering
2. **基建层非重复**:agent/turn/session 散落但通过 `_state` 单例 + event bus 已解耦;turn 是 bus+多消费者(非重复实现)→ 抽 kernel 非必需
3. **capability 层已存在**:`src/harness/capabilities/` 8 组件清晰 → 不需重设计
4. **event bus 是 HookRegistry 雏形**:`on_turn_start`/`on_turn_end` 多消费者注册已存在 → 轴 2 非从零建

### 7.3 修正后(本 plan)
- 轴 1 降级:删死代码 + ToolLayer 决策(非四层重构)
- 轴 2 降级:泛化 bus + 补 event(非重建)
- 轴 3 收窄:defer + 先验证(非预设 ACP)

### 7.4 保留的有价值洞察(grill 不全白做)
- **skill = 发现机制 + fusion**(轴 1 C5,defer 实施):外部 CLI + 脚本 + 三方 + io 原语融合,模型自主发现。现有 SkillCapability defer load 是雏形。
- **session 不统一改严格管理层**(轴 3,条件):不同 harness session 结构不同,建注册表 + 投影(若 turn 404 反复出)。
- **event bus 复用**(轴 2):不新建 HookRegistry,泛化现有 MemoryEventBus。
- **ACP 判据:驱动 vs 内容声明式**(轴 3,三次修正 2026-07-25):fan-out/spawn 是**硬编码驱动流程**(确定性内核调度)**不走 ACP**;A2A consume peer 是**内容声明式**(agent 间协议)**走 ACP**(A2A over ACP,A2A ⊂ ACP)。投影 = 驱动**状态 → hook event**(观测,axis2 SUBAGENT_STOP 已做),**非驱动走 ACP**。详见 axis-3-acp-channel.md §0/§7 + adr/axis3-acp.md。

## 8. 实施建议
- 阶段 1 先行(删死代码零风险,立收益)
- 每阶段回归:guardrail/memory/a2a/workflow 行为 + 全套件 pytest
- 边界守护:不改 claw ws / 不动 workflow_engine.py·flow.py / RK11
