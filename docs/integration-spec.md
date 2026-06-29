# agent-os-v2 记忆系统接入模块改进 Spec

> 基于:《memory-integration-whitepaper.md》(业界对标)+ 能力边界调研 + 代码自查(2026-06-21 单 agent audit,文件:行号证据)。
> **目标**:改进【记录 write / 召回 recall / 三方接口 integration】三块,让记忆系统对接外部 harness 时 **cache 友好 + LLM 可控编辑 + 接口标准化**。
> **红线**:蝴蝶翼 / 信任域 / 五维评分 不动;语义召回 SEMANTIC 待决策(§2.3)。
>
> **实施进度**(分支 `feat/memory-integration`,从 `feat/memory-kernel` 开):
> - ✅ **P1 完成(2026-06-21)**:R2 compiler 直注 user 尾部(commit `4e04256`)+ W3 写并发池+per-agent 保序(commit `86b976f`)。核心测试 51 通过,红线(蝴蝶翼/信任域/五维/provenance)回归通过。
> - ⏳ P2/P3 待开工(W2 / R4 / I3 / I4 / I5 / R1 SEMANTIC 移除)。

---

## 0. 现状吻合度总结

### ✅ 已具备(无需改)
| 项 | 证据 |
|---|---|
| W1 origin provenance(P0) | `types.py:52-66` + 6 巩固路径过滤 origin=AGENT(forgetting/migrator×6/state_pruner/consolidator/curator/ingestor 早返回) |
| R3 固定前段冻结 | static_count 隔离(`compiler.py:84/97`)+ breakpoint 落 static 末(`prompt_cache.py:72`) |
| R5 cache_control 双通道 | OpenAI 静默 / Anthropic 显式 breakpoint(`llm_client.py:80-160`) |
| I1 MemoryEventBus + 6 钩子 | `hooks.py:185-203`(session_start/turn_start/turn_end/pre_compress/session_end/delegate) |
| I2 降级开关 | `MEMORY_EVENT_BUS_ENABLED`(`engine.py:94-95`)+ 5 side-agent 独立 feature gate |
| I6 chat.py 直接内存调用清零 | grep 验证 chat.py 中 `memory_service.`/`memory_migrator.` = 0 处 |

### ⚠️ 偏差(实现与目标不符,需调整)
| 项 | 偏差 |
|---|---|
| R1 三模式 | SEMANTIC 死分支(`semantic_recall.py` 零引用、`vector_store=None`、`service.py:90` 注释"removed");实际 KEYWORD/UNIFIED(+KG) |
| R2 compiler 注入位置 | ~~注入 `role=system`,靠 llm_client 兜底降级(仅 Anthropic;OpenAI 仍 system)~~ → ✅ **已修复**(commit `4e04256`):compiler 直注 user message 尾部(围栏),双通道一致 |
| R4 LLM 工具双轨 | harness 预取 ✅;LLM 工具轨未打通(`KGMemoryTool`/`ExperienceTool` 存在但**未接 LLM tool_defs**) |
| I3 对外 adapter | 仅内嵌 REST 路由(`api/routes/memory.py`),直接操作 `_state`,无独立 adapter/SDK 层 |

### ❌ 缺口(需新增)
| 项 | 缺口 |
|---|---|
| W2 self-editing memory tool | 无 LLM 写/改/删记忆工具(仅 2 只读工具且未接 tool_defs) |
| W3 写路径后台串行化 | ~~用 asyncio.create_task/await emit,无串行/drain/inline fallback~~ → ✅ **已实现**(commit `86b976f`):并发池(Semaphore N)+ per-agent Lock + drain + inline fallback |
| I4 scope 映射层 | MemoryScope 仅内部字段,REST 直接透传,无 → 外部四 id/namespace 映射 |
| I5 MCP recall 工具 | 全仓无 MCP server |
| R6 FAISS/向量召回 | `vector_store=None`,运行时走 KG(`engine.py:57-59` 注释) |

### 红线确认
- **蝴蝶翼** ✅(`butterfly_wing.py` F1-F6/B1-B6/C1-C6 完整)
- **信任域** ✅(`permissions.py` + `_crud.py:76-86` 跨 scope 校验 + `SharedRecall`)
- **五维评分** ✅(`scorer.py:31-35` 权重 0.25/0.15/0.25/0.15/0.20)
- **语义召回三模式** ⚠️ SEMANTIC 死分支,待决策(§2.3)

---

## 1. 记录 Write 改进

### 1.1 W3 写路径并发池 + per-agent 保序 【P1 · ✅ 已实施 commit 86b976f】
**现状**:写路径用 `asyncio.create_task`(`chat.py:105/405/501`)+ `await bus.emit`(同步),**无统一队列** → ① 同 agent 写入顺序不保证(turn N 巩固可能跑在 N+1 后);② shutdown 在途写可能丢;③ 无背压。
**目标**:**多 agent 并发写 + 同 agent 保序 + drain + inline fallback**。
> ⚠️ **不用 hermes 的 `max_workers=1`** —— 那是单 agent 全局串行,多 agent 接入会成瓶颈(所有 agent 写挤一根管子)。agent-os-v2 是多 agent 接入平台,改成**并发池 + per-agent 锁**。
**改动**:
- 新增 `memory/write_queue.py`:`MemoryWriteQueue`(async 原生,贴合 FastAPI/asyncio 栈)
  - `asyncio.Semaphore(N)` 限全局并发(N=`MEMORY_WRITE_CONCURRENCY`,默认 8,按 agent 数调)
  - `dict[agent_id, asyncio.Lock]` per-agent 保序(同 agent turn N 先于 N+1;跨 agent 并发)
  - `_inflight: set[Task]` + `drain(timeout=_SYNC_DRAIN_TIMEOUT=5.0)`(shutdown 等在途写完成)
  - inline fallback:queue 关停/故障 → 同步 `await write_fn()`("丢异步不丢写")
  - 非致命:单写失败 log + 不阻塞其他(对齐 event_bus 非致命扇出 `event_bus.py:136-145`)
  - lazy lock 创建(全局 `_dict_lock` 保护,避免多 agent 首次建锁竞争)
- 改 `default_hook.py`/`chat.py`:`asyncio.create_task(write)` → `await write_queue.submit(agent_id, write_fn)`
- 改 `engine.py` shutdown:`await write_queue.drain(timeout=5)`
- 配置:`MEMORY_WRITE_CONCURRENCY`(默认 8)、`MEMORY_WRITE_DRAIN_TIMEOUT`(默认 5)
**核心逻辑**:
```
async submit(agent_id, write_fn):
    if self._shutdown: await write_fn(); return      # inline fallback
    lock = await self._get_lock(agent_id)            # per-agent,lazy
    async with lock:                                  # 同 agent 串行(保 turn 序)
        async with self._semaphore:                  # 跨 agent 限并发 N
            task = asyncio.create_task(write_fn())
            self._inflight.add(task)
            try: await task
            finally: self._inflight.discard(task)
```
**对标**:hermes `memory_manager.py:611`(串行骨架)+ `527-534`(298s 阻塞血泪史)+ `589-602`(inline fallback)—— **借鉴稳健性(drain/fallback/非致命),修正并发模型(单 agent max_workers=1 → 多 agent 并发池+per-agent lock)**
**验收**:① N agent 并发写吞吐 ≈ N(不互相阻塞);② 同 agent turn N 写入先于 N+1(lock 保证);③ shutdown 5s 内 drain 完在途写;④ queue 故障 inline 同步写不丢;⑤ 单写失败不阻塞其他 agent

**实现注意(自检补)**:
- `agent_id` 来源:`submit(agent_id, ...)` 的 agent_id 从 hook 上下文(session.agent_id / event.agent_id)传入;chat.py 的 turn_end/ingest 等 hook 需携带 agent_id —— **实施时确认 session 上下文已有,无则补**
- drain 期间新写:`_shutdown=True` 后新 submit 走 inline 同步(`await write_fn`,不进 inflight set);`drain` 只等已进 inflight 的在途写,inline 写由调用方自行 await 保证完成
- compiler 并发安全(关联 §4.1):✅ **已验证** ContextManager.select 只读 recall 无共享可变状态(`test_multi_agent_concurrent_compile_no_race`:5 agent 并发 compile 无竞争 + static 前缀字节稳定)

### 1.2 W2 self-editing memory tool 【P2】
**现状**:无 LLM 写/改/删记忆工具(仅 KGMemoryTool/ExperienceTool 只读 + 未接 tool_defs);记忆 CRUD 全 harness 内部完成。
**目标**:Anthropic 式 memory tool —— LLM 可 CRUD 记忆,**内容作 tool_result(不进 system,cache 友好)**;写入标 origin + 信任域校验。
**改动**:
- 新增 `memory/tools/memory_tool.py`:`MemoryTool`(memory_read / memory_write / memory_replace / memory_delete)
- 接入 LLM tool_defs(一并接 KGMemoryTool/ExperienceTool,或建 ToolRegistry)
- 写入:标 `origin=AGENT`(后台沉淀)+ 信任域校验(跨 scope 写需权限)+ 触发五维打分
- 记忆内容作 tool_result 返回(不进 system prompt)
**对标**:Anthropic `memory_20250818` + Letta `memory_replace` —— **取其 cache 友好的 tool_result 路径,弃 Letta core-常驻-system 的 cache 杀手**
**验收**:① LLM 能 read/write/replace/delete;② 写入标 origin=AGENT 不污染 FOREGROUND;③ 跨 scope 写受信任域拦截;④ tool_result 不破坏 system 前缀缓存
**红线对冲**:LLM 自编辑可靠性(Letta 短板:幻觉写错常驻块)—— 用 **信任域 5 级 + origin provenance + 五维打分** 对冲

---

## 2. 召回 Recall 改进

### 2.1 R2 compiler 直注 user message 尾部 【P1 · ✅ 已实施 commit 4e04256】
**现状**:compiler dynamic 段注入 `role=system`(`compiler.py:114-117`),靠 `llm_client._to_anthropic` 兜底降级 user(**仅 Anthropic 通道**);**OpenAI 通道仍 system**(破坏前缀)。
**目标**:compiler **直接产出 user message 尾部注入**(不靠 llm_client 兜底),OpenAI/Anthropic 一致。
**改动**:
- 改 `context/compiler.py:102-117`:dynamic 段从"插 role=system 消息"改为"追加到当前 user message content 尾部",用围栏 `<memory-context>[System note: ... NOT new user input. authoritative reference data ...]</memory-context>`
- 改 `llm_client.py _to_anthropic`:移除 dynamic system→user 降级兜底(compiler 已直注)
- static 段不变(固定前段冻结)
- (可选)新增流式 scrubber(跨 delta 剥围栏标签,hermes `StreamingContextScrubber`)
**对标**:hermes `conversation_loop.py:721-732`(召回进 user message 尾部)+ `memory_manager.py:296-310`(围栏)
**验收**:① dynamic 在 user message 尾部(双通道一致);② static 前缀字节稳定,cache 命中;③ 移除 llm_client 兜底后 cache_read 仍命中(轮2 cache_read 保留)
**风险**:改注入位置影响 cache,必须测 Anthropic cache_read(这是 P1 的硬验收)

### 2.2 R4 LLM 工具主动召回双轨 【P2】
**现状**:harness 预取 ✅(compiler 每轮 top_k=5);LLM 工具轨未打通。
**目标**:双轨 —— harness 预取(基线)+ LLM 工具主动召回(增量)。
**改动**:把 `KGMemoryTool`/`ExperienceTool` 接入 LLM tool_defs(或新建 recall 工具),LLM 工具循环内可主动召回。
**对标**:hermes `hindsight_recall` 工具(`plugins/memory/hindsight/__init__.py:264`)+ prefetch 双轨
**验收**:① LLM tool_defs 含 recall 工具;② LLM 可主动调增量召回;③ harness 预取仍基线兜底

### 2.3 R1/R6 SEMANTIC 决策 【P3,需拍板】
**现状**:SEMANTIC 死分支(`semantic_recall.py` 零引用、`vector_store=None`、`service.py:90` 注释"removed");FAISS 代码在但未接。
**决策二选一**:
- **方案 A(推荐,诚实)**:移除 `RecallMode.SEMANTIC` 枚举 + 删 `semantic_recall.py`/`vector.py`/`embedding.py` 死代码;诚实标注"KEYWORD + KG 双路径";pgvector 作为 future
- **方案 B(重接)**:接 pgvector 启用 SEMANTIC,恢复三模式(工作量大:embedding + pgvector 部署)
**✅ 已定方案 A(2026-06-21 用户拍板:向量暂无明确优势,有方向再说)**。KG 召回(蝴蝶翼多跳)已覆盖语义需求,FAISS 维护负担不值。方案 B(pgvector 重接)留作 future,有明确向量需求再做。

---

## 3. 三方接口 Integration 改进

### 3.1 I3 独立 adapter/SDK 层 【P2】
**现状**:仅内嵌 REST 路由(`api/routes/memory.py`),直接操作 `_state.memory_service`/`event_bus`,无独立 adapter 层/SDK 封装。
**目标**:独立 adapter 层,封装 event_bus + service + scope_mapper,脱离 _state 直接操作。
**改动**:
- 新增 `memory/adapter/`:`MemoryAdapter`(recall/store + emit + scope_mapper)
- 改 `api/routes/memory.py`:走 MemoryAdapter(而非 _state 直接操作)
- (可选)SDK client(sync/async Python)
**对标**:hermes `MemoryProvider` ABC(总线契约)+ Letta client SDK 命名空间
**验收**:① REST 经 adapter;② adapter 可独立测试(脱离 FastAPI);③ (可选)SDK client 可调

### 3.2 I4 scope 映射层 【P2】
**现状**:MemoryScope 四级(AGENT/SESSION/WORKSPACE/GLOBAL)仅内部字段,REST 直接透传(`memory.py:49`),无 → 外部映射。
**目标**:scope 映射层,4 级 scope → 外部四 id(user_id/agent_id/run_id/app_id)/namespace。
**改动**:
- 新增 `memory/scope_mapper.py`:`ScopeMapper`(to_external / from_external)
- 映射表:AGENT→agent_id、SESSION→run_id、WORKSPACE→app_id/namespace、GLOBAL→global
- 改 `api/routes/memory.py store`:走 mapper(而非透传)
**对标**:Mem0 四 id(user/agent/run/app)+ LangGraph namespace 点分。**反面对照**:Mem0 session_id(文档)vs run_id(API)错位(issue #3855)——映射要 API/文档/心智三者统一
**验收**:① 外部四 id 可映射回 4 级 scope;② REST store 用 mapper;③ 跨 harness(Mem0/LangGraph)映射无损

### 3.3 I5 MCP recall 工具 【P3,可选】
**现状**:无 MCP server。
**目标**(可选):暴露 MCP recall 工具(`defer_loading=true`),给 Claude Code/Cursor 类客户端;巩固/状态机在自管进程。
**改动**:
- 新增 `memory/mcp_server.py`:MCP server,暴露 `recall(scope_id, query, top_k)`(defer_loading=true)
- scope_id 显式入参(不依赖 MCP session,SEP-2575 无状态)
- 巩固/状态机/四优势在自管进程(**不寄托 MCP**)
**对标**:SEP-2575 无状态 + advanced-tool-use defer_loading + 白皮书 §3.4
**验收**:① recall 工具 defer_loading;② scope 显式入参;③ 巩固不寄托 MCP
**红线**:MCP = L2 上限,复杂能力在自管进程

---

## 4. 红线保护

| 红线 | 现状 | 改进中保护 |
|---|---|---|
| 蝴蝶翼 | ✅ F1-F6/B1-B6/C1-C6 | 不动 |
| 信任域 | ✅ permissions.py + 跨 scope 校验 | W2 memory_tool 写入走信任域 |
| 五维评分 | ✅ 权重 0.25/0.15/0.25/0.15/0.20 | W2 写入触发打分 |
| 语义召回 | ⚠️→✅ KEYWORD+KG 双路径 | SEMANTIC 已定按方案 A 移除(2026-06-21 用户拍板),物理删除待执行(P3)——枚举/semantic_recall.py/vector.py/embedding.py 仍在,生产零引用(死分支) |

### 4.1 贯穿约束:多 agent 并发接入
agent-os-v2 是**多 agent 接入平台**(区别于 hermes 单 agent runtime),所有接入层按"多 agent 并发"设计:
- **写入**:W3 并发池 + per-agent 保序(§1.1)
- **召回**:compiler 预取并发安全(多 agent 同时 compile,scope 隔离,无共享可变状态竞争)
- **scope**:agent_id 隔离(4 级 scope × 多 agent,I4 映射层支持)
- **adapter**:REST/SDK 并发安全(多请求并发,无全局可变状态)
- **状态机/巩固**:scope 隔离(全局日扫但按 agent_id/scope 过滤不混淆;无需 per-agent 进程,`MemoryFilter.scope` 保证隔离)

---

## 5. 优先级 + 依赖 + 工作量

| 优先级 | 改进 | 依赖 | 工作量(估) |
|---|---|---|---|
| **P1 ✅** | R2 compiler 直注 user message | 无 | ~3d → commit `4e04256` |
| **P1 ✅** | W3 写路径并发池+per-agent 保序 | 无 | ~3d → commit `86b976f` |
| **P2** | W2 memory tool(self-editing) | 信任域(已有) | ~4d |
| **P2** | R4 LLM 工具双轨 | tool_defs 接入 | ~2d |
| **P2** | I3 adapter 层 | 无 | ~2d |
| **P2** | I4 scope 映射 | 无 | ~1.5d |
| **P3** | I5 MCP recall | I3 adapter | ~2d |
| **P3** | R1 SEMANTIC 决策(A 移除) | 无 | ~0.5d |

**依赖**:R2/W3 独立(P1 可并行);W2/R4/I3/I4 可并行(P2);I5 依赖 I3。
**总估**:P1 ~6d(R2 3d + W3 3d),P2 ~9.5d,P3 ~2.5d,合计 ~18 人天(含测试 + 红线回归 + 多 agent 并发压测)。

---

## 6. 风险

1. **R2 compiler 改注入位置**:影响 cache 行为,硬验收 = Anthropic cache_read 轮2 命中保留。失败则回滚兜底(llm_client 降级)。
2. **W2 LLM 自编辑可靠性**(Letta 短板:幻觉写错):信任域 + origin + 五维对冲;memory_tool 写入必走权限校验。
3. **W3 写串行化**:fire-and-forget 改 enqueue,测写入不丢(inline fallback);daemon drain 不阻塞 shutdown。
4. **R1 SEMANTIC 决策**:若移除枚举,更新文档/测试(诚实标注双路径);若"三模式"是硬红线则重接 pgvector(工作量陡增)。
5. **I5 MCP 无状态**:巩固/状态机不寄托 MCP(自管进程),recall 工具 defer_loading 保 cache;scope 显式入参(反 mem0-mcp 硬编码 'cursor_mcp' 教训)。

---

## 附:对标来源
- **hermes**(本地 `/home/yy/tools/hermes-agent`):`conversation_loop.py:721-732`(注入)、`memory_manager.py:611`(写串行化)、`296-310`(围栏)、`skill_provenance.py`(provenance)
- **Anthropic**:memory_20250818 tool、context editing、prompt caching 严格前缀
- **Mem0/Letta/Cognee/MCP/Zep/LangGraph**:《memory-integration-whitepaper.md》§3 三轴 + 能力边界调研
- **agent-os-v2 现状**:本 spec §0(2026-06-21 单 agent audit,文件:行号)

---

*配套:《memory-iteration-plan.md》(P0-P4 已完成)→ 本 spec(接入模块改进,P1-P3 待开工)。*
