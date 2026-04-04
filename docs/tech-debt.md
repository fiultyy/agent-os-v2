# 技术债务清单 — Agent OS

> 最后更新: 2026-04-05
> 当前评分: 9.0/10, 完成度 95%

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
