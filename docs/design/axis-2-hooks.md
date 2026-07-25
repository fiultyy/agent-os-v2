# 轴 2:hooks 补全 — 泛化现有 event bus + 补缺失 event(非从零建)

Date: 2026-07-25(第三轮读代码后回退重写)
Status: Design(轻补全,待 plan)
依赖:无

**判断结论(2026-07-25 读代码后)**:现有 `memory event bus` + `on_turn_start`/`on_turn_end` 多消费者注册(observe_hook/neural_field/runtime_observer/default_hook/hooks.py)**已是 HookRegistry 雏形**。**不是从零建**,是**泛化 bus + 补缺失 event**。曾设计的"从零建 HookRegistry 对标 Claude 27"基于"现有 hook 零散无 registry"的前提,**部分证伪**(bus + register 机制已存在)→ 降级为补全。

关联:轴 1(工具调用 fire PreToolUse/PostToolUse)、轴 3(ACP session/turn event 经 bus)

## 1. 现状(有证据)

### 1.1 已有的 hook 机制(雏形,复用)
- `MemoryEventBus` + `EventType`(TURN_END/SESSION_END/INGEST)+ `register(handler, *event_types)` 多消费者
- 已注册消费者:observe_hook / neural_field / runtime_observer / default_hook / hooks.py(TurnContext)
- memory lifecycle → observe(MemoryObserveHook OBSERVER 转发)

### 1.2 缺失/硬编码(真问题)
| event | 现状 |
|---|---|
| PreToolUse | ⚠️ guardrail input **硬编码在 ToolExecutor**,非注册式 |
| PostToolUse | ⚠️ guardrail output 硬编码 |
| PostToolUseFailure | ❌ executor except 直接返 error,无 fire |
| UserPromptSubmit | ❌ trigger_turn 入口无 fire |
| Stop | ❌ agent.run 结束无 Stop fire |
| SubagentStop | ❌ workflow/a2a consumed agent 结束无 fire |
| PermissionRequest/Denied | ⚠️ guardrail block 隐含,无显式 flow |

**核心问题**:Tool 层 hook 硬编码(executor 内直调 guardrail),非 bus 注册式;Tool/Stop/Submit/Subagent event 缺失。

## 2. 补全项(轻量,非重建)

### H1 泛化 bus 到全生命周期(复用机制)
- `EventType` 扩:`TOOL_PRE`/`TOOL_POST`/`TOOL_POST_FAIL`/`TURN_SUBMIT`/`TURN_START`/`STOP`/`SUBAGENT_STOP`/`PERMISSION`
- bus 机制不变(register + emit + 多消费者 + OBSERVER priority)
- 可选:bus 改名 HookRegistry(或留 MemoryEventBus 名,加别名)— ponytail 倾向不改名(回归面)

### H2 fire 点接入
| fire 点 | event | 位置 |
|---|---|---|
| ToolExecutor.execute | TOOL_PRE → run → TOOL_POST / TOOL_POST_FAIL | executor.py(替代硬编码 guardrail) |
| REST trigger_turn 入口 | TURN_SUBMIT | routes turn 端点首行 |
| agent.run 前/后 | TURN_START / STOP | native agent 包装 |
| workflow/a2a consumed 结束 | SUBAGENT_STOP | transport.py/workflow |

### H3 消费者迁移
- guardrail:ToolExecutor 硬编码 → 注册 TOOL_PRE/TOOL_POST handler(可 block)
- memory/observe:已是注册式(不动)
- permission:guardrail block 时 fire PERMISSION_REQUEST/DENIED

## 3. 实施路径(2-3 轮)
- **P0 EventType 扩展 + fire 框架**:复用 bus,加 Tool/Stop/Submit/Subagent event。单测 fire 顺序/deny 短路。
- **P1 fire 点接入 + 消费者迁移**:ToolExecutor(Pre/Post/Fail 替代硬编码 guardrail)+ trigger_turn(Submit)+ agent.run(Stop)。guardrail 注册式。
- **P2 SubagentStop + Permission**:workflow/a2a consumed fire SubagentStop;guardrail block fire Permission。

## 4. 验收
- [ ] EventType 覆盖 Tool/Turn/Stop/Submit/Subagent/Permission(memory bus 已有 Turn/Session/Ingest)
- [ ] guardrail 注册式(无 ToolExecutor 硬编码)
- [ ] PreToolUse deny 短路;PostToolUseFailure fire(当前 executor except 无 fire)
- [ ] 现有 guardrail/memory/observe 行为不变(回归)

## 5. 风险 / defer
- PreCompact/Notification defer(AO2 暂无压缩/TUI 侧)
- Stop vs TurnEnd 语义:agent 主动停 vs turn 结束 — P2 明确区分
- bus 改名 HookRegistry:回归面,defer(留 MemoryEventBus 名)

## 6. 回退记录
**曾设计**:从零建 HookRegistry + HookEvent 枚举(对标 Claude 27 全 fire)+ 消费者全迁移。

**证伪**:memory event bus + register 多消费者**已是 HookRegistry 雏形**(非零散)。Tool 层是硬编码(真问题),但 Turn/Session 已注册式。

**降级**:不重建,泛化 bus EventType + 补 Tool/Stop/Submit/Subagent + guardrail 注册式。
