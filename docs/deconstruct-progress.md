# Agent OS v2 — Deconstruct 实施进度

> ⚠️ **历史文档**(写于当时, 记录当时的实现计划/进度状态)。当前系统现状以 `docs/mvp-iteration-roadmap.md` + `agentos-mvp-roadmap` memory 为准; 正文可能含已变更 / 已 defer / 已重构 的内容, 仅供历史参考。

> 整体进度: **~8%**（Phase 0 全部完成，Phase 1 待启动）
> 最后更新: 2026-05-02 08:10

---

## Phase 状态总览

| Phase | 名称 | 任务数 | 状态 | 进度 |
|-------|------|--------|------|------|
| Phase 0 | 准备 | 4 | ✅ COMPLETED | 100% |
| Phase 1 | @agent-os/frame | 6 | ⬜ NOT_STARTED | 0% |
| Phase 2 | 独立 Packages | 11 | ⬜ NOT_STARTED | 0% |
| Phase 3 | Pi Extensions | 8 | ⬜ NOT_STARTED | 0% |
| Phase 4 | Frame Scope | 6 | ⬜ NOT_STARTED | 0% |
| Phase 5 | 集成测试 | 6 | ⬜ NOT_STARTED | 0% |
| Phase 6 | 清理 | 7 | ⬜ NOT_STARTED | 0% |

---

## Phase 0 — 准备

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P0-1 | Pi mono repo 分析 | ✅ COMPLETED | 2026-04-30 | pi-ai / pi-agent-core / pi-coding-agent 核心模块清单 |
| P0-2 | Agent OS 依赖矩阵 | ✅ COMPLETED | 2026-04-30 | 4 替代 + 8 保留 + 3 桥接 |
| P0-3 | 创建 worktree | ✅ COMPLETED | 2026-05-01 | `~/projects/agent-os-v2` 分支 `feat/pi-deconstruct` |
| P0-4 | 解构规格文档 | ✅ COMPLETED | 2026-05-01 | spec + tasks + progress 三文档完成，commit `92d9421` |

---

## Phase 1 — @agent-os/frame

> 定义所有跨 package 共享的 TypeScript 接口和类型

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P1-1 | 定义 `IMemoryStore` 接口 | ⬜ NOT_STARTED | — | get/set/delete/search/clear |
| P1-2 | 定义 `IKnowledgeGraph` 接口 | ⬜ NOT_STARTED | — | entity/relation CRUD + 图遍历 |
| P1-3 | 定义 `IGraphEngine` 接口 | ⬜ NOT_STARTED | — | compile/run/reset |
| P1-4 | 定义 `IEventSink` 接口 | ⬜ NOT_STARTED | — | emit/subscribe/dispose |
| P1-5 | 定义共享类型: Tick, Branch, Event, AgentBlock | ⬜ NOT_STARTED | — | Python dataclass → TS type |
| P1-6 | 实现 FrameContext DI 容器 | ⬜ NOT_STARTED | — | 基于 Pi EventBus |

---

## Phase 2 — 独立 Packages

> 将 Python 核心数据层提取为独立 TypeScript/Python 包

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P2-1 | `@agent-os/memory` — SQLiteStore 移植 | ⬜ NOT_STARTED | — | Python→TS，实现 IMemoryStore |
| P2-2 | `@agent-os/memory` — KnowledgeGraph 移植 | ⬜ NOT_STARTED | — | entities+relations 双表 + CTE |
| P2-3 | `@agent-os/memory` — Compressor 移植 | ⬜ NOT_STARTED | — | 11→3 压缩逻辑 |
| P2-4 | `@agent-os/memory` — Sideline 移植 | ⬜ NOT_STARTED | — | JSONL→ActionUnit→KG |
| P2-5 | `@agent-os/memory` — Python binding 层 | ⬜ NOT_STARTED | — | napi-rs 或 HTTP FFI |
| P2-6 | `@agent-os/observe` — EventStore 移植 | ⬜ NOT_STARTED | — | event sourcing |
| P2-7 | `@agent-os/observe` — BranchStore 移植 | ⬜ NOT_STARTED | — | SQLite CRUD + merge/prune |
| P2-8 | `@agent-os/observe` — TickTracker 移植 | ⬜ NOT_STARTED | — | tick 计数 + 分支追踪 |
| P2-9 | `@agent-os/tools` — FileTool 移植 | ⬜ NOT_STARTED | — | Pi AgentTool 格式 |
| P2-10 | `@agent-os/tools` — DBTool 移植 | ⬜ NOT_STARTED | — | Pi AgentTool 格式 |
| P2-11 | `@agent-os/tools` — BrowserTool 移植 | ⬜ NOT_STARTED | — | 浏览器进程管理 |

---

## Phase 3 — Pi Extensions

> 将 Agent OS 能力注册为 Pi extension

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P3-1 | `frame.ts` extension | ⬜ NOT_STARTED | — | 注册 FrameContext 到 Pi |
| P3-2 | `memory.ts` extension | ⬜ NOT_STARTED | — | registerTool: memory_recall, kg_query |
| P3-3 | `observe.ts` extension | ⬜ NOT_STARTED | — | PiEventBridge: Pi→Canvas 事件 |
| P3-4 | `orchestrate.ts` extension | ⬜ NOT_STARTED | — | Hook Pi lifecycle |
| P3-5 | `profile.ts` extension | ⬜ NOT_STARTED | — | BaseProfile.compile() 注入 |
| P3-6 | `tools.ts` extension | ⬜ NOT_STARTED | — | 注册 File/DB/Browser Tool |
| P3-7 | PiSessionManager | ⬜ NOT_STARTED | — | 多 session 管理 + JSONL↔SQLite |
| P3-8 | Python ↔ Pi 桥接层 | ⬜ NOT_STARTED | — | napi-rs/HTTP Session Pool |

---

## Phase 4 — Frame Scope Packages

> 高层编排能力

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P4-1 | GraphStateMachine | ⬜ NOT_STARTED | — | 节点执行 + 状态转换 |
| P4-2 | MetaAgentNode | ⬜ NOT_STARTED | — | ConditionalSpawner + Sandbox |
| P4-3 | ScoringEngine | ⬜ NOT_STARTED | — | ButterflySignalPolicy |
| P4-4 | ConditionalSpawner | ⬜ NOT_STARTED | — | 条件触发子 agent |
| P4-5 | LayerCompiler | ⬜ NOT_STARTED | — | 三层 Profile 编译 |
| P4-6 | PluginRegistry | ⬜ NOT_STARTED | — | 能力插件注册/发现 |

---

## Phase 5 — 集成测试

> 端到端验证

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P5-1 | Pi + 单 extension 集成测试 | ⬜ NOT_STARTED | — | 逐个 extension 加载验证 |
| P5-2 | Pi + 全 extension 集成测试 | ⬜ NOT_STARTED | — | 所有 extension 协同 |
| P5-3 | 纯 Python 独立运行验证 | ⬜ NOT_STARTED | — | 原有 E2E 等价通过 |
| P5-4 | 前端 WS 集成测试 | ⬜ NOT_STARTED | — | Canvas 事件 + Branch/Tick |
| P5-5 | 多 Agent 并发场景测试 | ⬜ NOT_STARTED | — | 3+ 并发 Pi session |
| P5-6 | 性能基准对比 | ⬜ NOT_STARTED | — | 新 vs 旧性能对比 |

---

## Phase 6 — 清理

> 删除旧模块，更新文档

| Task | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|----------|------|
| P6-1 | 删除 LLMClient | ⬜ NOT_STARTED | — | Pi pi-ai 替代 |
| P6-2 | 删除 ToolExecutor | ⬜ NOT_STARTED | — | Pi agent-core 替代 |
| P6-3 | 删除 Engine.py 主循环 | ⬜ NOT_STARTED | — | 500→~100 行 |
| P6-4 | 删除 EventEmitter | ⬜ NOT_STARTED | — | PiEventBridge 替代 |
| P6-5 | 更新架构文档 | ⬜ NOT_STARTED | — | architecture.md / STATUS.md |
| P6-6 | 更新 CI/CD | ⬜ NOT_STARTED | — | TS 构建 + Python 桥接 |
| P6-7 | 迁移指南 | ⬜ NOT_STARTED | — | v1→v2 breaking changes |

---

## 问题与决策记录

| # | 日期 | 问题 | 决策 |
|---|------|------|------|
| D1 | 2026-04-30 | Pi (TS) vs Agent OS (Python) 跨语言桥接 | 待定 — napi-rs / HTTP / 子进程 |
| D2 | 2026-04-30 | Pi 单 session vs Agent OS 多 Agent 并发 | Session Pool 方案 |
| D3 | 2026-04-30 | Session 持久化双写一致性 | PiSessionManager JSONL↔SQLite |

---

## 当前阻塞项

1. **D1 待定**: 跨语言桥接方案未最终确认（影响 P2-5 和 P3-8）

---

## 关键路径

```
P0-4 → P1-6 → P2-1 → P3-7 → P4-2 → P5-2 → P6-3
```

---

*最后更新: 2026-05-01 13:00*
