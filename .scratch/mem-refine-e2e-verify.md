# AO2 记忆提炼链真实 e2e 验证报告

- **日期**: 2026-08-04 ｜ **HEAD**: 874da0c ｜ **任务**: 验证 59695b1 归档重接的 side agent 在 native AO2 下真跑通
- **范围**: 仅 agent-os-v2,未碰 openclaw/claw。仅验证,未改代码。
- **环境**: observe :8002 + orch :8001(全 MEMORY_*_ENABLED=1 + SIDE_LLM_ENABLED=1 + MEMORY_EVENT_BUS_ENABLED=1,清 SOCKS,真智谱 GLM)
- **LD_PRELOAD**: orch 必须带 `LD_PRELOAD=/lib/x86_64-linux-gnu/libsqlite3.so.0`(miniconda sqlite 损坏,start.sh 同款;`/usr/bin/python3` 无 pysqlite3)

## 真实 turn(POST /h/agent-os-v2/sessions/e2e-mem-verify/turn,sync)
- message: `我叫张三,我最喜欢用 Rust 写系统软件,平时用 git 管理代码。请简短确认你记下了。`
- GLM 返回 status=completed,确认记下张三/Rust/git。session auto-create(默认 agent d6afc973…)。

## 验证点实测

| 验证点 | 期望 | 实测 | 判定 |
|---|---|---|---|
| a. orch.log 提炼链 | IngestorHook/extract 证据 | `IngestorAgent parse failed: empty_parse — degrading`(1 次,非致命降级)+ neural field anomaly 'Rust' ×2 | ⚠️ 见下 |
| b. /v1/memory/graph 非空 | total_nodes>0(之前=0) | 端点实际返回 `nodes` 列表(无 total_nodes 字段);带参 `?entities=Rust,张三&agent_id=e2e-mem-verify` → **nodes=26 edges=121**(baseline 0) | ✅ PASS |
| c. KG 实体+记忆增长 | entities/memories 增长 | **kg.db entities 437→544(+107,持续涨) / relations 411 / 触达 45 个源 memory**;memories.db memories 4025→4028(+3) | ✅ PASS |
| d. /v1/memories?query=Rust 走 RetrieverHook | match×lif 排序非 fallback | 返回 2 条(我的 turn memory)但**无 score 字段** → 实走 `service.recall` fallback,非 RetrieverHook | ❌ 见「发现#1」 |

### 实体语义质量(LLM 提炼生效的硬证据,非 regex 降级)
- `张三`(identity): `{role:user, name_confirmed:true, preference_language:Rust, preference_domain:系统软件, tool:git}`
- `Rust`(technical_term): `{domain:programming_language, preference_level:favorite}`
- `git`(technical_term): `{domain:version_control, usage:daily}`
- `系统软件`(concept): `{context:software_development}`

→ 这些富属性字段(name_confirmed/preference_level/usage)是 LLM 语义推断产物,regex 降级路径不可能产生。**我的内容走通了 LLM 语义提取**。`empty_parse` 是另一条低内容 memory 的非致命降级(regex 兜底),不影响主路径。

## 整体判定: **PASS(核心链路通),1 项预存 wiring 缺陷报告**

59695b1 重接的 side agent **真跑通**:
- **IngestorAgent ✅ 强验证**:KG +107 实体、触达 45 源 memory、富语义提取(非降级)
- **RetrieverAgent ✅**:经 graph 端点直接调 `_state.retriever.retrieve`(26 nodes)证明已接线且工作
- **Neural field ✅**:Rust 概念异常检测(0.817/1.000 potential)
- **Observe ✅**:turn 生命周期事件 96 条(tick_started/94×token_delta/tick_completed)
- **ConsolidatorAgent ⚠️ 已接线但本场景未触发**:memory_versions=0、memory_blocks 不变(172→172)。单 turn 不发 CONSOLIDATE/SESSION_END 事件,故 SEMANTIC 凝结未被演练 —— 非失败,仅未被此场景命中
- CuratorAgent / task_consolidator:已接线(engine.py gate on),单 turn 不发 CURATE/task 事件,未触发

## 发现(报告,未修)

### #1 [预存缺陷,非 59695b1 回归] /v1/memories?query= 无法走 RetrieverHook scored 路径
- **根因**: `services/orchestrator/src/services/_state.py:97` `async def fire(...) -> None` —— fire-and-forget,**丢弃 `bus.emit` 的返回值恒返 None**。
- 而 `MemoryEventBus.emit`(event_bus.py:145-169)**会**返回 last non-None hook 结果(RetrieverHook.on_recall 的 ranked 列表)。
- `src/api/routes/memory.py:168` `ranked = await _state.fire(EventType.RECALL, ctx)` 拿到 None → 永远走 `service.recall` fallback(line 173),RetrieverHook 的 match×lif scored 路径**经此 HTTP 端点不可达**。
- **不对称**: graph 端点(memory.py:248)直接调 `_state.retriever.retrieve(detail=True)` 绕过 fire → 工作正常(26 nodes);只有 `/v1/memories?query=` 被 fire 卡死。
- **来源**: `git log -S` 追溯 fire 的 `-> None` 与 route 的 `_state.fire(EventType.RECALL` 均出自 **2c560c5(axis2 hooks)**,早于 59695b1。即 59695b1 重接 retriever 本身正确,此不可达是更早的预存 wiring gap。
- **影响**: retriever side agent 已正确接线且能跑(graph 证明),只是 `/v1/memories` HTTP 通道拿不到它的 scored 输出;查询结果仍由 `service.recall` 返回(内容正确但无 score)。

## 附注
- task 步骤 7b 的 `total_nodes` 字段不存在 → 实际字段是 `nodes` 列表(测 len);步骤 7c 的 `entities` 表在 `data/kg.db` 不在 `memories.db`(memories.db 只有 memories/memory_blocks/memory_versions/sessions)。
- orch app logger 未配置(`logger.info/warning` 不进 orch.log,只有 print 进),故「side-agent LLM wired」(engine.py:137 logger.info)与 ConsolidatorAgent logger 不可见 —— 是日志配置问题非未接线。
- 后端仍在运行: orch :8001(pid in /tmp/orch.log) / observe :8002。可 `pkill -f 'uvicorn src.engine:app'` / `pkill -f 'uvicorn src.app:app'` 清理。
