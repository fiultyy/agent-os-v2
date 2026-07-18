> ⚠️ **历史快照(gateway 已退役,2026-07-18)**:本文档含 `:8000` curl 引用指向已删除的 gateway BFF(commit c594969)。现役服务:orchestrator `:8001` / observe `:8002` / native `/h`。文中 `:8000` 示例为失效死引用,不再维护。

# Agent-OS-V2 MVP 迭代规划

> **基于 2026-06-27 代码全景(ultracode `who348jwq`,14 模块对抗 verify)+ 规划 workflow(`wtp0gugjp`,7 断裂簇分析 → 路线图综合 → 3 视角对抗审查)重写。**
> **取代** `docs/implementation-roadmap.md`(其"98% 完成"声明已证实严重失实,见 §6)。
> **排除记忆模块** —— 已在 `feat/memory-integration` 独立迭代完成,不在本规划范围。
> **完成判据**:端到端可跑通的链路(§5 Phase 3 五步验收),**非模块数**。

---

## 1. MVP 北极星

**端到端连通可用的单 agent 对话 + 工具调用 + 记忆召回 + 可观测** —— 而非堆功能。当前项目是"骨架丰满、接线稀疏"的中期原型:核心对话链路在 orchestrator 单体可跑,但围绕它有大量接线断裂与已实现孤岛未通电。MVP 聚焦三件事:**① 解锁全链路 → ② 点亮 MVP 相关孤岛 → ③ 清理误导性死代码**。投入产出比极高(代码俱在,只差装配/常量/调用)。

### boundary_in(MVP 必达)

> ✅ **迭代后状态(2026-06-28 回填)**:下述 7 条目标经 Phase 0/1 + defer(FC/多轮/PitFail)**全部达成**。各条保留作"目标/设计说明",末尾 ✅ 标实际交付(commit 见 §4/§5)。Phase 3 ✅ 已验证(`:verify` 部署 8010 + 单轮/多轮 e2e 真 LLM glm-4.7 anthropic,见 §4 + multi-agent-poweron-roadmap L8)。

1. **gateway→orchestrator 全量转发补 `/v1` 前缀**(解除生产全 404)+ 前端 `/api/*` → next rewrite → gateway → orchestrator `/v1/*` **三段链路端到端可达**(审查补:原规划漏了前端 /api 这段) — ✅ Phase 0(`124bc67`,config 加 ORCHESTRATOR_API + 18 处改引用 + 首个 gateway 回归测试)
2. `/v1/chat` 单轮对话 + `/v1/execute` SSE 流式(主路径已实现,只差 gateway 接线) — ✅ Phase 0 接线 + defer FC 升级为 LLM 原生 function-calling
3. **前端实时通路点亮**:Canvas WS 经 `next.config` rewrite 可达 orchestrator `/ws/canvas`;`canvasStore.sessionId` 闭环(WS 连接成功回写 store,且 live 页 sid 来源明确);**Layer2 协议对齐** —— ⚠️ 实际是前端发 `type:'layer2.submit'`、后端 `canvas.py:196` 读 `cmd`,统一为前端改 `cmd`(审查纠正:原规划方向写反) — ✅ L3(`be4688f`)
4. **Agent CRUD 经 gateway 可用且持久化重启不丢** —— ⚠️ 非"一行装配":`_state.py:42` 注释 `pg_store removed — was never fully wired`。需**双向通电**:engine 启动期实例化 PostgresStore + `initialize()` + **启动 hook 从 PG 灌回 `_state.agents`**(审查 critical:无此灌回则重启验收必假阳性)+ `agents.py` 路由改读 pg — ✅ L2(`3d5532b`,PostgresStore + restore_agents_from_pg 双向,本地 engine import 冒烟 OK)
5. **L3 工具链最小通电**:engine 实例化 ToolRegistry 并 register 已实现工具;⚠️ **修正 `chat.py:232-233` 默认 `tool_name=web_search`(必 not found)与 `tool_args={query:...}`(与 handler 签名 `http_get(url,...)` 不匹配)**;Guardrail.check_output 支持 dict 脱敏;system prompt 引导 LLM 输出 `tool_call:<name>`(否则正则永不命中) — ✅ L2(18 工具 register + 默认对齐 + Guardrail dict)+ defer FC(原生 tool_use 取代正则启发式)
6. **Agent 间通信面板点亮**:`register_agent` 在 session 建立时调用(使 broadcast 收件人非空)+ `register_delivery_callback` 桥 AgentMessage 到 chat.py SSE + 前端 dispatch `agent_message` 进 debugStore — ✅ L2 通信桥(`register_delivery_callback`→SSE)+ L4 前端 dispatch(`b6026cc`)
7. **可观测基线**:执行历史/通信面板有真实数据;gateway 转发矩阵回归测试(防 `/v1` 前缀漂移) — ✅ gateway 转发回归测试(6 passed)+ 前端实时通路就绪(端到端真实数据待 Phase 3 容器)+ observer 双层已交付(`RuntimeObserverHook` OBSERVER hook + `_state.runtime_observations`(cap 200)+ SSE `runtime_observation` + `/debug/status` `runtime_anomalies`,前端 `sse-dispatch.ts`/`debugStore`(cap 200)/`DebugPanel`(10s 轮询))

### boundary_out(明确排除)

| 项 | 处置 | 理由 |
|---|---|---|
| gRPC(100% 未接线) | defer | 无 MVP 消费场景,纯文档承诺 |
| 高级编排抽象(Flow/DAG/Loop/Cron) | defer | 生产单线性图够用;接通是"实现"非"接线"。注:ParallelNode/FanInNode 已于 P3(`386aae2`)/v1/orchestrate 编排终点通电,移出 defer |
| meta agent 三件套(ConditionalSpawner/MetaAgentNode/SandboxExecutor) | defer(标 NOT-WIRED 保留) | 需完整 meta 生命周期;注 `create_subagent` 本身已于 P3(`386aae2`)/v1/orchestrate 接通生产 spawn→run→teardown(`agent_manager.py:67`),仅 ConditionalSpawner/MetaAgentNode/SandboxExecutor 三件套仍 NOT-WIRED |
| 信任域 ScopeManager / Checkpoint resume / CommunicationBus 持久化 | defer | MVP 单轮跑完即结束,不需要 |
| 蝴蝶翼写侧 / PitFail / scoring-committee / CAContextCoding | defer(MVP 后第一优先通电) | 属记忆进化簇后续 |
| control 拦截+推理层 / ContextManager.write-compress-isolate / LLMNode+ToolCallNode mock / canvas 死事件 / 前端 CommitteeVoteNode | **delete** | 纯死代码(Phase 2 清理) |
| 三辅助后端(observer/rm/pm)持久化与接线 | defer/归档 | orchestrator 零调用纯孤岛 |
| LLM 原生 function-calling | defer(架构改造) | MVP 靠正则/显式 tool_call 字段驱动够演示 |

---

## 2. 现状基线(对抗 verify 已确诊)

> ✅ **迭代后状态(2026-06-28 回填)**:下述"接线断裂"经 Phase 0/1 + defer(FC/多轮/PitFail)**全部修复** —— gateway `/v1` 解锁、前端 WS/Layer2 通路、工具链 18 工具 register、CommunicationBus broadcast 投递、PostgresStore 持久化双向(写 + 启动灌回)、LLM 原生 function-calling + 多轮 tool_use loop、PitFail 通电。下列诊断保留作**"迭代前基线"档案**(实际完成状态见 §4);末尾"真正生产就绪"清单仍准。

骨架丰满、接线稀疏的中期原型。核心对话链路(`orchestrator /v1/chat` + `/v1/execute` SSE + LLM + memory 召回)在 orchestrator 单体可跑,但:

- **gateway→orchestrator 全量业务转发裸路径缺 `/v1`**(生产全 404)
- **前端 Canvas WS / Branch(session 恒空→422)/ Layer2(协议错位)三断**
- **L3 工具链整链悬空**(ToolRegistry 零注册,唯一调用 web_search 未注册)
- **CommunicationBus 空壳**(register_agent/receive 零调用 → broadcast 收件人恒空)
- **Agent 状态全内存重启即丢**(PostgresStore 已实现但 `_state.py:42` 标 removed)
- **大量孤岛未通电**(蝴蝶翼/PitFail/scoring-committee/MetaAgent/control/CAContextCoding)
- **三辅助后端 orchestrator 零调用**(纯孤岛)
- **gRPC 100% 未接线**
- 旧 `implementation-roadmap.md` 声称 98% 完成严重失实

**真正生产就绪**:gateway 鉴权(RS256/refresh/限流)· `/execute` 4 节点图 · `/chat` · LLMClient 双通道 · ContextCompiler.compile · memory 召回排序 · memory 五维评分 · CanvasEventStore · ConcurrencyController · SSE 事件总线 · 前端 JWT/Flow 画布。

---

## 3. 迭代线(worktree 分线)+ 依赖

> `can_parallel_with` 语义 = **文件层面无直接重叠(可同 worktree 切)**,非"可独立合并"。`depends_on` 非空的线,合并必须等依赖先行。

| 线 | worktree 分支 | 簇 | depends_on | 文件层面可并行 |
|---|---|---|---|---|
| **L1** | `fix/gateway-v1-prefix` | 网关路由 | — | L3, L6 |
| **L2** | `feat/orchestrator-mvp-wiring` | 工具链 + 通信 + **持久化(原 L5 降级并入)** + 编排 | L1 | —(orchestrator 热点串行) |
| **L3** | `fix/frontend-canvas-realtime` | 前端实时通路 | L1 | L1, L6 |
| **L4** | `fix/frontend-comms-panel` | 前端通信消费侧 | L3 | L1, L6 |
| **L6** | `chore/deadcode-cleanup` | 死代码清理 | — | L1, L3, L4(全程并行,events.py 除外) |
| **L7** | `feat/island-poweron-postmvp` | 孤岛通电(post-MVP) | L2 | — |

**关键修正(审查)**:
- 原 L5-persistence **取消独立 worktree**,降级为 L2 子任务 —— 持久化通电必触 engine.py 启动装配(`line 90-250` 集中区)+ agents.py 路由,与 L2 在 engine.py 装配区直接冲突,无法独立并行。
- L2 **独占 `chat.py` / `engine.py` / `_state.py` 写权**(吸收工具默认值 + 通信桥 + 持久化装配全部改动)。
- `next.config.ts` **硬约束**:L1 禁动(只动 gateway),L3 独占 rewrites 数组(加 `/ws/canvas`)。

---

## 4. 分阶段收敛

### Phase 0 — 全链路解锁【L1】· 纯接线零设计 ✅ 已完成(2026-06-27,commit `124bc67` / merge `965051c`)

**目标**:解除生产全 404。gateway→orchestrator 所有业务转发补 `/v1` 前缀(18 处:agents.py 5 + chat 1 + execute 1 + memories 4 + messages 2 + kg 3 + debug 2)。

- **方案 B(推荐)**:`config.py` 新增 `ORCHESTRATOR_API = f"{ORCHESTRATOR_URL}/v1"` 常量(L1 独占 config.py),各 route 改引用 —— 单点改前缀,便于将来 `/v2`。仅 orchestrator 转发加 `/v1`,三辅助后端裸路径不动。
- **merge_order**:L1 **最先合并**;其他依赖线必须基于 L1 合并后再开 worktree(防前缀漂移复现)。
- **conflict_files**:`services/gateway/src/config.py`(L1 独占)+ 7 个 `routes/*.py`
- **validation**:
  - `services/gateway/tests/test_routes.py`(新建,首个 gateway 测试)用 httpx MockTransport/respx 断言每个转发落到 `{ORCHESTRATOR_URL}/v1/<path>`(防漂移)
  - curl 经 gateway:8000 验证 7 端点非 404(GET /agents 返回 200 且 JSON 结构合法 —— **不依赖具体 agent 内容**,与 L2 持久化解耦)
  - `cd services/gateway && pytest tests/test_routes.py`

### Phase 1 — MVP 骨架通电【L2(含持久化)+ L3 + L4】· ✅ 完成(L3 `be4688f` + L4 `b6026cc` + L2 `3d5532b` 全 merge 主线 `384183e`;22+6 测试绿,L2 stash baseline 零回归证明)

> ✅ **L2 遗留已澄清(2026-06-27 冒烟验证)**:① PG schema —— 对抗验证 P2 误判,`initialize()` 的 `metadata.create_all` 已建 4 表(memory_items/memory_blocks/sessions/**agents**),首启不缺表;② backfill —— 无需,`create_agent_data`→`store_agent`(写 PG)+ `_init_pg_store_and_restore`→`restore_agents_from_pg`(读灌回,不覆盖)+ `init_default_agent`(若 _state.agents 非空则跳过),重启不丢对新建 agent 闭环成立;③ **本地 engine import 冒烟通过**(pysqlite3+httpx 环境:`ENGINE IMPORT OK`,`tool_executor` 注册 **18 工具**,`pg_store` 无 DATABASE_URL 降级 None)。生产容器冒烟(带真 PG + server 启动)待 Phase 3 验收。

**目标**:点亮 MVP 的可用与可观测。三线并行启动,但 orchestrator 热点(chat.py/engine.py/_state.py)由 L2 独占串行。

**L2(feat/orchestrator-mvp-wiring)—— 最重,最后合并吸收所有 orchestrator 改动**:
- 工具链:`engine.py:117` 后 register 已实现工具(清单制:列出 primitive+skill+composite 各项,跳过重依赖的 browser_flow/code_review,日志打印实际数与清单比对 —— **不硬编码"26"**);修正 `chat.py:232-233` 默认 tool_name/tool_args 与 handler 签名对齐;Guardrail.check_output 支持 dict;system prompt 引导 `tool_call:<name>`
- 通信:`register_agent`(session 建立时)+ `register_delivery_callback` 桥 SSE + 前端 dispatch
- **持久化(原 L5,双向通电)**:engine 启动期 `PostgresStore` 实例化 + `await initialize()`(失败降级 None,保留 guard 语义)+ **启动 hook 从 PG 灌回 `_state.agents`** + `agents.py` list/get 改读 pg
- engine.py 装配区插入点:**工具 register 块插在 `line 117` 后、`line 118`(communication_bus)之前**(审查:line 118-244 已被 memory_event_bus/write_queue/5 side-agent 占满)

**L3(fix/frontend-canvas-realtime)**:`next.config.ts` 加 `/ws/canvas` rewrite(独占);`canvasStore.sessionId` 闭环(wsClient.onopen 回写 store + live 页 sid 来源明确);**Layer2 前端改 `cmd`**(`layer2Store.ts:7,38` type→cmd)

**L4(fix/frontend-comms-panel)**:依赖 L3 的 canvasStore 改动落地;dispatch `agent_message` 进 debugStore

- **merge_order**:L1(前置)→ L3-frontend → L4-frontend-comms(依赖 L3)→ **L2-orch-serial 最后合并**(吸收工具/通信/持久化全部 chat.py/engine.py/_state.py 改动)
- **conflict_files**:`chat.py`(L2 独占,头号冲突源)· `engine.py`(L2 独占)· `_state.py`(L2 独占)· `canvasStore.ts`(L3→L4 串行)· `next.config.ts`(L3 独占)
- **validation**:
  - 前端 `/canvas/live` WS 状态未连接→已连接,sessionId 非空;Layer2 提交后端进 `layer2.submit` accepted 分支(不再 unknown cmd)
  - 工具:mock state 触发 `_node_tool` 对每个已 register 工具调用一次,断言不 not found 且不因 tool_args key 不匹配 TypeError(审查:register 数≥N 不够,要端到端)
  - `POST /v1/agents` 创建 → `docker compose restart orchestrator` → `GET /v1/agents` 仍含该 agent(**持久化双向验证**)
  - 通信面板非空,broadcast 投递数 >0
  - 新增单测:`test_communication.py` / `test_persistence.py`(create→restart→list 一致性)/ `test_graph_engine.py`(4 节点线性 + 条件边 + **`_resolve_next` 多边 bug** —— 审查建议从 defer 提到此 Phase 修,`graph/__init__.py:475-480` for-edge-return-first)

### Phase 2 — 死代码清理 + 文档归位【L6】· ✅ 完成(`c5b9f9a` / merge `1ad0c5d`,19 文件 +34/-1432 **净删 1398 行**,对抗验证 6 点全 PASS)

**目标**:消除误导性死代码,让"98% 谎言"归位。可与 Phase 0/1 任意并行(独占文件为主)。

- **⚠️ 强制前置(审查 critical)**:删除 `coding_context.py`/`codebase_context.py`/`git_context.py`/`pitfail_context.py` 四文件**之前**,必须先改 `context/__init__.py:5-17`(删 4 import)+ `:22`(__all__ 移除),否则 `engine.py:42` `from src.context import ...` 崩 ImportError。同理删其他被 re-export 的文件前先改对应 `__init__.py`。
- 删除:control 拦截/推理层 · ContextManager.write/compress/isolate(保留 select)· LLMNode/ToolCallNode mock(保留 FunctionNode)· canvas ScoringSignalEvent/CommitteeVoteEvent · 前端 CommitteeVoteNode
- **events.py 协调**:L6 删死事件与 L3 读用 CanvasEvent/TickStartedEvent 共享 `canvas/events.py` —— **L6 在 L3 合并后做删除**(L3 先只读不删),或把死事件删除移到 L3
- **文档归位**:`implementation-roadmap.md` 删"98% 完成"声明,改反映实际;D-21/D-19/D-15 标 defer/未通电
- **merge_order**:L6 最后合并;协调 `context/__init__.py`(若记忆簇后续也动 context)
- **validation**:`grep -r 'InterceptLayer|ReasoningLayer|CAContextCoding|PitfallContextBuilder|LLMNode|ToolCallNode' services/orchestrator/src/` 零命中(自身定义已删);`pytest -q` 无 ImportError;`cd apps/web && npm run build` 无报错

### Phase 3 — MVP 验收 + 基线收敛【L1+L2+L3+L4+L6 合并主分支】· ✅ **部署验证通过**(2026-06-28);本地冒烟装配完整(engine import OK + 18 工具 + pitfail/memory/kg,orchestrator **collect 基线 729 tests**(`pytest --co`,HEAD 5fe1bee;含 1 可容忍 collection error = canvas e2e requests 噪声,doc_check.py 设计内 tolerant);实跑 **715 passed / 14 failed**(失败项均预存 = butterfly_wing 测试隔离 + test_phase7 ModuleNotFoundError,零新增回归) <!-- DOC-CHECK: tests=729 --> [锚点=collect 数(稳定可重现 `pytest --co`),非执行 passed 数(环境性 baseline 抖动);CI 回写见 `scripts/doc_check.py`],gateway 6 passed);**容器部署验证通过**(2026-06-28:`:verify` 镜像临时容器 8010,/health + /v1/agents[默认助手 restore] + /v1/pitfall/ + /v1/memories 全可达,PitfailRegistry wired,启动无 ImportError;生产 `:latest` 22h 容器未触碰,记忆数据零风险)

**MVP 交付闸门** —— 端到端五步(可复现命令记入 `docs/mvp-acceptance.md`):

1. 经 gateway:8000 打开前端,创建自定义 agent(POST /agents 落 PG)
2. 与该 agent 多轮对话(/chat + /execute SSE),LLM 在显式 tool_call 下发起工具调用(http_get/file_read 等)并返回合成结果(**不再 web_search not found**)
3. 对话中记忆召回生效(召回结果影响 system prompt)
4. 前端 Canvas 实时显示 tick/tool 事件流,CommunicationPanel 显示通信,ExecutionHistory 显示历史(三者非空)
5. `docker compose restart orchestrator` 后自定义 agent 仍在(持久化);对话历史不要求保留(Checkpoint defer)

- **merge_order**:L1(解锁前置)→ L6(死代码,低冲突)→ L3-frontend → L4-frontend-comms → **L2-orch-serial 最后**(最重,吸收所有 orchestrator 改动)。遵循"独占文件先行 + 共享热点后行"。
- **validation**:主分支端到端冒烟全绿(上述 5 步)+ `pytest -q`(orchestrator)+ gateway `pytest tests/` + `npm run build` + `docker compose up` 全服务健康。

---

## 5. defer 分组(MVP 后,按反馈动态启)

| 里程碑 | 内容 | 前置 |
|---|---|---|
| **记忆进化通电**(第一优先) | ✅ PitFail `92be2cd`/merge `df225ca`:registry 实例化 + 工具失败 hook(match→increment复发 / record新)+ /v1/pitfall API(4 GET)+ _classify_tool_error,9 测试绿;蝴蝶翼写侧接线待(记忆簇后续,蝴蝶翼红线需先与记忆迭代分支协调) | — |
| **LLM function-calling + 多轮 loop** | ✅ 完成(FC `c248894`/merge `5f9b22b` + 多轮 `8cdc48b`/merge `7a87b68`):chat tools 参数 + tool_use 解析(anthropic 原生 + openai 兼容)+ 正则降级兜底 + 18 工具 schema;多轮(tool→llm 循环 + tool_result 回注 + MAX_TOOL_ITERATIONS + 死循环 bugfix)。12 测试绿;collect 基线 **729 tests**(`pytest --co`,HEAD 5fe1bee;实跑 715 passed / 14 failed,失败项均预存 baseline 零新增回归;多 agent 编排 P0-P3 后,2026-06-28) <!-- DOC-CHECK: tests=729 --> | done |
| **多 Agent 编排** | ✅ **已通电**(`874da6a` 修 BFS fan-in join-barrier + 18 单测;`25e802e` P0 create_subagent/teardown_subagent 生命周期 + R1/R5 守卫;`78d6f5c` P1 MetaAgentNode run_agent_turn 剥离记忆;`386aae2` P3 `/v1/orchestrate` 集成终点,2026-06-28):POST `/v1/orchestrate`(engine.py:535 挂载)→multi_agent(ParallelNode: N×AgentWorkerNode 各 `create_subagent`(`agent_manager.py:67`)→`run_agent_turn`(真实 LLM)→`teardown_subagent`(`:133`)finally 必跑)→fan_in(FanInNode)→synthesizer(`run_agent_turn(orchestrator_id)` 综合);经 `_agent_manager_shim()`(`api/routes/orchestrate.py:59-73`)复用 agent_manager 实现,与 `/v1/execute`、`/v1/execute_parallel` 物理隔离(R2,自带 `_sse` helper,**不碰 chat.py**);产 SSE `agent_status`/`node_start`/`node_complete`/`execution_complete`;信任域 ScopeManager / Checkpoint resume 仍随本簇 defer | 图引擎基线(Phase 1 `_resolve_next` + `874da6a` join-barrier)✅ + P0-P3 编排集成✅ |
| **辅助服务命运决策** | ✅ **归档**(`07f9921`,2026-06-28):compose 解耦 gateway depends_on + `profiles:[aux]` 隔离 + 三路由 502 兜底,代码全保留可逆;⚠️ `profiles` 仅 Compose v2 生效(本机 v1.29.2 静默忽略,但 depends_on 解耦已使主链路不被 aux 拖垮,核心收益达成);通电待真实需求(prompt 面板/会话回放落库/多 provider 动态路由);gRPC 无消费场景继续 defer | 视 MVP 反馈 |

---

## 6. 旧 roadmap delta(为何失实)

旧 `implementation-roadmap.md` 声称 98% 完成,以 D-* 模块标记掩盖接线未通:

- **废弃/失实**:D-21(control 零调用,delete)· D-19(CAContextCoding 零实例化,delete)· D-16(meta 三件套完整;`create_subagent` 已于 P3 `386aae2` 接通,仅 ConditionalSpawner/MetaAgentNode/SandboxExecutor 断裂,标 NOT-WIRED defer)· "98%"总声明(改绑定可跑测试的 MVP 完成度)
- **重排延后**:D-15 蝴蝶翼(写侧零调用,降 Phase 5)· D-20 PitFail(Verifier 零集成,降 Phase 5)· D-25 BackwardWriter(随 D-15/D-20)
- **保留聚焦接线**:旧 D-* 全在加新功能,而 MVP 真实需求是"让已实现骨架通电"。Phase 0-1 把旧 roadmap 完全漏记的接线项(gateway /v1、工具 register、通信桥、PostgresStore、前端 WS)提到 P0/P1
- **完成判据更正**:旧以"模块数",本规划以"端到端可跑通"(Phase 3 五步)

---

## 7. 风险 + 执行注意

- **共享热点竞争**:`chat.py`/`engine.py`/`_state.py` 是头号冲突源 → L2 独占写权,串行合并,其他线禁碰
- **文档漂移复发**:✅ **已落地**(2026-06-28,`dde05de`+`3abf3a3`):`scripts/doc_check.py`(collect `pytest --co` / islands NOT-WIRED 扫描 / verify 锚点对比)+ `.github/workflows/doc-check.yml` + docs `<!-- DOC-CHECK: tests=729 -->` 锚点;解 kg 审核 HIGH 根因(文档数字漂移),漂移 CI 红;`pyproject.toml [build-system]` 移位修(`3abf3a3`)解 CI 首跑必红
- **审查遗留 medium/low**(执行时注意):L1 替换 18 处(非 17)· `can_parallel_with` 语义澄清(文件无重叠 ≠ 可独立合并)· 工具数量清单制(非硬编码 26)· PostgresStore.initialize() 建表与记忆 SQLite 双库并存评估
- **执行编排**:Phase 1 的 L2/L3/L4 用 **tmux 多 session + 多 worktree 并行**(L2 独占 orchestrator,L3/L4 前端线),收敛节点按 §4 merge_order 合并

---

*生成:2026-06-27 · 基于 ultracode 全景 `who334jwq` + 规划 workflow `wtp0gugjp`(7 簇分析 + 路线图综合 + 3 视角对抗审查,11 agent / 62 万 token)*
*原始产出:`/tmp/claude-1000/.../tasks/wtp0gugjp.output`*
