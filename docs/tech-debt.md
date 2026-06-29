# 技术债务清单 — Agent OS

> 最后更新: 2026-06-29
> 当前评分: 9.5/10, 完成度 97%
> P4 新增: TD-007/009(记忆迭代文档对齐);TD-008 已修复移入已解决
> Phase 2 L6: TD-005 移入已解决;新增 ✅TD-R15(死代码清理)
> 低优先级区原 TD-001/002/003/004/006 均已修复,移入「已解决」区(TD-00x / TD-R16)

## 记忆迭代文档对齐（P2，2026-06-20 P4 新增）

### TD-007: FAISS 声明与实现不一致
- **文件**: `services/orchestrator/src/memory/service.py`(`vector_store=None`)+ `docs/`(D-12/D-27)
- **问题**: `vector.py` + `embedding.py`(all-MiniLM-L6-v2,384 维)代码完整存在,但 `service.py` 传 `vector_store=None`、`__init__.py` 未导出 SemanticRecall → 运行时未启用,已切 KG(unified = 关键词 + KG)。D-12/D-27 文档声明"已移除 FAISS"与代码(还在)不符。
- **影响**: 文档误导(误以为有向量召回);代码冗余。
- **校准**(P3 已做): 保留 FAISS 代码为 V2 pgvector 预留,`service.py` docstring 明确"vector_store=None,KG 为主,FAISS 代码保留但运行时未启用"。本条记录真相。
- **优先级**: P2

### TD-009: skill bundle 未对齐 agentskills.io
- **文件**: 项目当前无 SKILL.md(`find` 无结果)
- **问题**: 项目未实现 skill bundle,无法对齐 [agentskills.io](https://agentskills.io/specification) 开放标准(SKILL.md = YAML frontmatter `name`+`description` + Markdown body)。
- **影响**: 无法跨 agent 互通 skill。
- **修复**(P4): 文档化 agentskills.io 标准;后续若实现 skill 遵循该规范。
- **优先级**: P2

## 已解决

### ✅ TD-R1: engine.py 绕过 StateGraph (已修复 round 1)
### ✅ TD-R2: 通信/并发模块空壳 (已修复 round 1)
### ✅ TD-R3: 3 个后端服务空壳 (已修复 round 1)
### ✅ TD-R4: 前后端脱节 (已修复 round 1)
### ✅ TD-R5: Docker 配置错误 (已修复 round 1)
### ✅ TD-R6: Provider 适配器空壳 (已修复 round 2)
### ✅ TD-R7: LLM 节点硬编码 (已修复 round 2)
### ✅ TD-R8: Dockerfile COPY 语法 (已修复 round 2)
### ✅ TD-R9: flowStore 架构不一致 (已修复 round 2)
### ✅ TD-R10: Provider 无重试 (已修复 round 3)
### ✅ TD-R11: Tool 节点硬编码 (已修复 round 3)
### ✅ TD-R12: import json 在循环内 (已修复 round 3)
### ✅ TD-R13: Header.tsx TODO (已修复 round 3)
### ✅ TD-R14: Dockerfile public 目录 (已修复 round 3)
### ✅ TD-005: InMemoryStore 无持久化 (已修复 — L2 落地 SQLiteStore)
- **文件**: `services/orchestrator/src/memory/sqlitestore.py`
- **修复**: `SQLiteStore`(持久化 SQLite 实现)已落地并经 engine 启动注入;`InMemoryStore` 保留为开发/测试回退。对应架构 D-15/D-19/D-20 的占位实现同步清理(见 TD-R15)。

### ✅ TD-R15: 死代码清理 (已修复 Phase 2 L6, 2026-06-27)
- **范围**: 移除早期占位/mock 实现,消除 __init__.py re-export 对死模块的依赖
- **删除**:
  - `services/orchestrator/src/control/intercept_layer.py` + `reasoning_layer.py`(D-15 三层控制占位,control/__init__.py 清空 re-export)
  - `services/orchestrator/src/context/coding_context.py` + `codebase_context.py` + `git_context.py` + `pitfail_context.py`(D-19/D-20 CAContextCoding 六层 Builder 占位,context/__init__.py 仅保留 CompiledContext/ContextCompiler/ContextManager)
  - `context/manager.py` 删 write/compress/isolate/get_isolated_context(无生产调用者,保留 select)
  - `graph/nodes.py` 删 LLMNode/ToolCallNode(mock 节点,保留 GraphNode/FunctionNode)
  - `canvas/events.py` 删 ScoringSignalEvent/CommitteeVoteEvent(canvas/__init__.py 同步移除 import + __all__)
  - `apps/web/src/components/canvas/nodes/CommitteeVoteNode.tsx` + TickCanvas nodeTypes 注册清理
- **验证**: engine import 冒烟通过 + grep 死符号零残留 + pytest 整体回归无 ImportError
- **关联**: architecture.md D-15/D-19/D-20 状态行已标注"实现代码已移除(defer),待模型落地重建"

### ✅ TD-001: import 位置优化 (已修复)
- **文件**: `services/orchestrator/src/tools/executor.py`
- **状态**: `asyncio` 已在模块顶层导入(第 9 行 `import asyncio`),全文无方法体内 import(grep `^( {4}| {8})(import |from )` 零命中)。原文档引用的第 119 行实为 `"error": None,`(execute() 成功返回 dict 字段),与 import 无关。

### ✅ TD-002: import 位置优化 (已修复)
- **文件**: `services/orchestrator/src/memory/forgetting.py`
- **状态**: `from datetime import datetime, timezone` 已在模块顶层(第 15 行)导入,方法体内无 import。原文档引用的第 157 行实为 `result.skipped_has_relations += 1`(has_related_memories 判断后的计数逻辑)。

### ✅ TD-003: Provider stream() 无重试 (已修复)
- **文件**: `services/resource-manager/src/providers/__init__.py`
- **状态**: 三个 provider 子类(OpenAIProvider/AnthropicProvider/ZhipuProvider)的 `stream()` 均实现连接建立级重试 —— `for attempt in range(MAX_RETRIES)` 循环包裹 `client.stream`,异常落到 `(HTTPStatusError, TransportError)` 后指数退避重试(见 line 104 注释 `# Retry connection establishment for stream`,line 106/217/290 stream 方法)。按原文档建议实现:仅重试连接建立阶段,不重试已发送的 chunk。

### ✅ TD-004: BaseProvider 抽象方法 (已修复)
- **文件**: `services/resource-manager/src/providers/__init__.py`
- **状态**: 已落地标准 ABC 模式。`class BaseProvider(ABC)`(line 49)+ `@abstractmethod` 装饰 `complete`/`stream`(line 62-67),全文无 `NotImplementedError`(grep 零命中)。原文档"抛 NotImplementedError""可改为 ABC"两处与当前代码矛盾,该债务已不存在。

### ✅ TD-006: JWT 黑名单内存存储 (已修复 — Phase 9 SQLite 持久化)
- **文件**: `services/gateway/src/routes/auth.py`
- **状态**: 已实现 SQLite 持久化(Phase 9 ✅)—— `revoked_jtis` 表存于 `data/auth.db`(WAL 模式),`_is_jti_revoked`/`_revoke_jti` 走 DB,仅当 SQLite 无法打开时回退到内存 `_fallback_jtis`(line 37)。原文档引用的 `_revoked_jtis` 符号已被替换(全局 grep 零命中),line 30 实为 `from src.middleware import rate_limit_auth`。重启不再丢失吊销状态。

### ✅ TD-R16: 架构对比覆盖不全 (已修复 P4, 2026-06-20)
- **文件**: `docs/architecture-comparison.md:357`
- **状态**: 已补齐「主流记忆系统对比」章节(P4),含 hermes-agent / Letta / Mem0 / Zep 六维对比表(记忆分层/召回/巩固/产权边界/事件解耦/cache)+ agent-os-v2 四优势总结,引用记忆研究报告第七、八章结论。原 TD-008「未系统覆盖」的问题描述已过时。
