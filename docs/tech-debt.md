# 技术债务清单 — Agent OS

> 最后更新: 2026-06-20
> 当前评分: 9.5/10, 完成度 97%
> P4 新增: TD-007/008/009(记忆迭代文档对齐,标 P2)

## 低优先级（不影响功能）

### TD-001: import 位置优化
- **文件**: `services/orchestrator/src/tools/executor.py:119`
- **问题**: `import asyncio` 在方法体内
- **影响**: 轻微性能开销
- **修复**: 移到模块顶层

### TD-002: import 位置优化
- **文件**: `services/orchestrator/src/memory/forgetting.py:157`
- **问题**: `from datetime import ...` 在方法体内
- **影响**: 轻微性能开销
- **修复**: 移到模块顶层

### TD-003: Provider stream() 无重试
- **文件**: `services/resource-manager/src/providers/__init__.py`
- **问题**: `complete()` 有重试逻辑，但 `stream()` 没有
- **影响**: 流式调用失败时不会自动重试
- **备注**: 流式重试复杂（需要处理已发送的 chunk），可后续优化

### TD-004: BaseProvider 抽象方法
- **文件**: `services/resource-manager/src/providers/__init__.py`
- **问题**: `BaseProvider.complete()` 和 `stream()` 抛 `NotImplementedError`
- **影响**: 无（抽象基类标准模式）
- **备注**: 可改为 ABC + @abstractmethod 更规范

### TD-005: InMemoryStore 无持久化
- **文件**: `services/orchestrator/src/memory/store.py`
- **问题**: 所有数据在内存中，重启丢失
- **影响**: 无法用于生产
- **修复**: Phase 9 SQLiteStore 替代

### TD-006: JWT 黑名单内存存储
- **文件**: `services/gateway/src/routes/auth.py:30`
- **问题**: `_revoked_jtis` 是内存 set，重启丢失
- **影响**: 重启后已吊销的 refresh token 可重用
- **修复**: Phase 9 SQLite 持久化

## 记忆迭代文档对齐（P2，2026-06-20 P4 新增）

### TD-007: FAISS 声明与实现不一致
- **文件**: `services/orchestrator/src/memory/service.py`(`vector_store=None`)+ `docs/`(D-12/D-27)
- **问题**: `vector.py` + `embedding.py`(all-MiniLM-L6-v2,384 维)代码完整存在,但 `service.py` 传 `vector_store=None`、`__init__.py` 未导出 SemanticRecall → 运行时未启用,已切 KG(unified = 关键词 + KG)。D-12/D-27 文档声明"已移除 FAISS"与代码(还在)不符。
- **影响**: 文档误导(误以为有向量召回);代码冗余。
- **校准**(P3 已做): 保留 FAISS 代码为 V2 pgvector 预留,`service.py` docstring 明确"vector_store=None,KG 为主,FAISS 代码保留但运行时未启用"。本条记录真相。
- **优先级**: P2

### TD-008: 架构对比覆盖不全
- **文件**: `docs/architecture-comparison.md`
- **问题**: 当前主要对比 Multica,未系统覆盖 hermes-agent / Letta / Mem0 / Zep 等主流记忆系统。
- **影响**: 架构选型参考不足,无法体现 agent-os-v2 的差异化优势。
- **修复**(P4): 补齐主流记忆系统对比(引用记忆研究报告第七、八章结论)。
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
