# 多 Agent 编排通电 · 里程碑路线图

> 基于 2026-06-28 规划 workflow(`w0zvipwnl`,6 区域评估 + synthesize,7 agent / 39 万 token)。
> **里程碑**:让 meta 三件套(ConditionalSpawner/MetaAgentNode/SandboxExecutor)+ ParallelNode 接生产图,实现真正多 agent 协作。
> **铁律**:`/execute` 单 agent 线性图零回归 + 记忆模块零触碰。
> ParallelNode 准备已完成(`874da6a`:join-barrier 修 + 18 单测)。
>
> ✅ **里程碑达成(2026-06-28)**:P0-P3 全完成,提交链 `25e802e`(P0 create_subagent)→ `78d6f5c`(P1 MetaAgentNode/run_agent_turn)→ `8fa2e1f`(P2 ParallelNode demo)→ `386aae2`(P3 /v1/orchestrate)。`POST /v1/orchestrate` → fan-out N subagent(spawn→run→teardown 闭环)→ fan-in 综合 → synthesizer,SSE 全程可见。**客观隔离判据**(kg 审核订正 2026-06-28):三文件(test_multi_turn_tool_loop/test_graph_engine/test_parallel_nodes)git diff 0 行 + `test_parallel_production_graph` 为本里程碑 P2 新增(324 行,非"零改动")。全套 **692 passed / 14 failed**(14 均既有 butterfly_wing/vectorstore 环境性 baseline,零新增回归;P1 修 B1 flake 使 15→14)。<!-- DOC-CHECK: tests=706 -->(锚点=collect 数 706,稳定可重现;执行 passed 692 受环境性 baseline 抖动,CI 回写以 collect 锚点为准,见 `scripts/doc_check.py`)红线 R1-R8 代码层全守。⚠️ **kg 审核诚实发现**:(a) ~~`meta_spawner` 休眠孤岛~~ **已清理**(`d1e64ef`:删 engine.py 装配块 + _state 槽位,生产经 _agent_manager_shim 绕过;ConditionalSpawner 类保留 defer);(b) ~~"记忆单点落库"幻觉~~ **编排记忆沉淀 ✅(ADR-3 done `b721532`/merge `0f1aa1e`)**:orchestrator fan-in 单点 emit TURN_END/SESSION_END+KG(复用既有 caller,R1 分支零记忆,env gate);engine.py memory_event_bus 生产已 wire(emit 被消费落库);召回侧闭环 ✅**done**(`72b4386`):机制已闭合(召回链全程 agent_id 严格过滤天然消费 orchestrator_id 沉淀,在评分上游,redline=none)+ 7 测试实证(写→召回往返/隔离负向/match×lif 公式守卫/origin 卫生,对抗突变验证真守卫)+ **编排自召回注入 ✅**(`6889a47`:_synth_handler 召回 orchestrator_id 历史注入 synth_input,沉淀→召回→注入综合(⚠️ 写侧默认✅ / 召回侧 gated `MEMORY_RETRIEVER_ENABLED=1` 默认 no-op;kg审核 wl28ya1l6 P1 诚实订正),只读 retrieve redline none,+3 测试);(c) `/v1/orchestrate` + `/execute_parallel` 无生产 caller(demo 端点孤岛,待消费);(d) ~~`orchestration_graph_builder` 纯死字段~~ **已删**(`d1e64ef`)。**清理**:`d1e64ef` 还删 DEGRADED_AGENTS 死字段 + _sse_error/build_skill_parser 死函数 + ~25 未用 import;10 defer 孤岛(SandboxExecutor/ScopeManager/ConcurrencyController/canvas API 等)加 `# NOT-WIRED` 标注。真实 HTTP 端到端冒烟 ✅(2026-06-28 `:verify` 容器 8010:真 LLM 多 agent researcher+writer→fan-in→synthesizer 综合 + ADR-3 编排记忆沉淀→`/v1/memories` 召回闭环验证;⚠️ 容器需配 openai 通道 env `LLM_BASE_URL=paas/v4` + `LLM_API_KEY`;生产 `:latest` 23h 未动)。

## 北极星判据

`POST /v1/orchestrate` → fan-out N 子 agent 并行跑 → fan-in 汇聚 → 综合输出,全程 SSE 可见,且 `test_multi_turn_tool_loop(6)` + `test_graph_engine(4)` + `test_parallel_nodes(18)` **三文件零改动全绿**。

---

## 0. 事实基线(已核实)

| 事实 | 证据 |
|---|---|
| `create_subagent` 接口不存在 | agent_manager.py 仅 4 CRUD(L13/43/53/67);conditional_spawner.py:218 `hasattr` 恒 False;fallback L242 把模块当 callable 必崩 |
| meta 三件套零生产引用 | grep ConditionalSpawner/MetaAgentNode/ParallelNode 排除自身零命中 |
| `_build_execution_graph` 被测试直接 import | test_multi_turn_tool_loop.py:149/322 → 改它=回归 |
| `_node_llm` 记忆触发密集 | chat.py 11+ 处 memory_event_bus.emit/_trigger_ingest/_trigger_kg |
| `_state` 全 None-safe 槽位 | 加新字段符合既有模式 |
| MetaAgentNode 是 stub | meta_agent_node.py:98 立即 set completion_event;L155 "Override for real integration" |
| join-barrier 已修 + 18 单测绿 | graph/__init__.py:513 pending-queue 去重 |

---

## 1. 分阶段(P0-P3,严格串行依赖)

```
P0 create_subagent 生命周期(地基)
   ├──→ P1 MetaAgentNode 真 execute(消费 P0)
   └──→ P3 调度入口 /v1/orchestrate(消费 P0+P1)
P2 ParallelNode demo(独立,可与 P1 并行)
```

### P0 · create_subagent 生命周期(地基,S 0.5-1 天)
- **改**:agent_manager.py 新增 `create_subagent`/`teardown_subagent`(不改 create_agent_data);conditional_spawner.py L241-256 删死 fallback;新增 test_agent_manager_subagent.py
- **契约**:`create_subagent(agent_type, config, parent_id="", session_id="") -> {"id":uuid}` / `teardown_subagent(agent_id) -> bool`
- **要点**:不复用 create_agent_data(它调 init_agent_blocks → 记忆红线);自建 dict + `is_subagent:True` 标记;teardown 必须 is_subagent 守卫
- **验证**:teardown 不删持久 agent / create_subagent 不调 memory / 返回契约 str+dict 双过

### P1 · MetaAgentNode 真 execute(M 1-1.5 天,依赖 P0)
- **改**:meta_agent_node.py override `_execute_agent`(保留 stub 兼容);更新 test_meta_agent_node stub 断言 + 新增真执行单测
- **要点**:抽公共 `_run_agent_turn(agent_id, input, session)`(禁止 import 改 _build_execution_graph);独立 agent_id + GraphState + session_id 隔离;completion_event 由真完成 set
- **风险**:`_run_agent_turn` 误复用 _node_llm 全套(11+ 记忆触发)→ 记忆红线 + 重复沉淀。缓解:分支节点禁用记忆写

### P2 · ParallelNode 接生产图 demo(M 1-1.5 天,独立可与 P1 并行)
- **改**:chat.py 新增 `_build_parallel_graph`/`_node_llm_for`/`/execute_parallel`(严禁改 L548/L678);models.py +ExecuteParallelRequest;新增 test_parallel_production_graph.py
- **要点**:每分支 FunctionNode + ParallelNode + FanInNode(concat);`_node_llm_for` 剥离所有记忆触发;fan-out 前 register_agent
- **客观判据**:test_multi_turn_tool_loop(6) + test_graph_engine(4) + test_parallel_nodes(18) 零改动全绿

### P3 · 调度入口 /v1/orchestrate(L 2-3 天,消费 P0+P1)
- **改**:新增 routes/orchestrate.py + orchestration/multi_agent_graph.py + agent_worker_node.py;models.py +OrchestrateRequest;_state.py +meta_spawner/+orchestration_graph_builder(None-safe);engine.py include_router
- **要点**:物理隔离(新文件/router/request);`_build_multi_agent_graph`(orchestrator→ParallelNode(AgentWorkerNode)→FanInNode→synthesizer);AgentWorkerNode 调 create_subagent + _run_agent_turn + teardown
- **客观判据**:三文件零改动全绿 + 端到端 curl 多 agent + 零主路径 diff + 记忆 R1 分支零触碰;orchestrator 综合轮单点 emit(ADR-3 `0f1aa1e`)

**推荐执行序**:P0 → P2‖P1 → P3(4-5 天优化路径)

---

## 2. Worktree 策略

碰 L2 独占写权区(chat.py/engine.py/_state.py)+ 长周期(5-7 天),**强烈建议独占 worktree** `feat/multi-agent-poweron`。每阶段原子 commit(带 ✅hash+区域+N 测试绿);P3 集成验证全绿后 PR 回主线。

---

## 3. 红线 R1-R8(铁律)

| # | 红线 | 落地 |
|---|---|---|
| R1 | 记忆模块零触碰 | create_subagent 严禁 init_agent_blocks;_node_llm_for/_run_agent_turn 剥离 memory_event_bus.emit/_trigger_*;**分支零落库**(run_agent_turn 不变);orchestrator 综合轮单点 emit(ADR-3 `0f1aa1e`,复用既有 caller,env gate) |
| R2 | /execute 线性图冻结 | _build_execution_graph(chat.py:548)+/execute(chat.py:678)一行不改 |
| R3 | 不给 ExecuteRequest 加 mode 开关 | 多 agent 用独立 OrchestrateRequest/ExecuteParallelRequest |
| R4 | L2 独占写权区最小侵入 | _state 只加 None-safe 字段;engine 只 include_router+实例化;chat 只加新函数 |
| R5 | teardown is_subagent 守卫 | 仅 is_subagent is True 才删 |
| R6 | create_subagent 返回契约锁定 | {"id":...} 或 str(spawner L223-228 固化) |
| R7 | spawner 独立 bus | 不接 _state.memory_event_bus |
| R8 | ConcurrencyController 复用 | 经 acquire_agent_slot,不自造并发原语 |

---

## 4. 对抗验证 + 回归矩阵

| 阶段 | 对抗验证 | 回归门 |
|---|---|---|
| P0 | teardown 不删持久 / create_subagent 不调 memory / 契约双过 | test_conditional_spawner(现有 Mock 不破坏) |
| P1 | 假阳性消除 / completion_event 真完成 set / 上下文隔离 | test_meta_agent_node(更新 stub 断言) |
| P2 | _node_llm_for 无记忆触发 / slot 不饿死 / clone 不丢 output / SSE par/fanin | **三文件零改动全绿** |
| P3 | 三文件零改动 / 端到端 curl / 零主路径 diff / 记忆单点 | **三文件** + test_multi_agent_orchestrate |

**客观隔离判据**(P2/P3):test_multi_turn_tool_loop + test_graph_engine + test_parallel_nodes 三文件 git diff 为空且全绿 = 隔离有效。

---

## 5. 风险 + 工作量

| 风险 | 等级 | 缓释 |
|---|---|---|
| 误改 _build_execution_graph//execute | 高 | 物理隔离 + 回归门锁三文件 |
| 误触记忆红线 | 高 | create_subagent 不调 init_agent_blocks;分支剥离记忆触发 |
| _state 全局单例并发竞争 | 中 | 子 agent 独立 agent_id + GraphState |
| MetaAgentNode stub 假成功 | 中 | override + completion_event 真完成 |

**总计**:5-7 天(串行)/ 4-5 天(P0→P2‖P1→P3)

**Defer(明确不做)**:ConditionalSpawner CRON/QUEUE(先 EVENT)/ SandboxExecutor 接节点 / 存量 backfill

---

*规划产出:`/tmp/claude-1000/.../tasks/w0zvipwnl.output`*
