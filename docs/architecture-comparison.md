# Agent OS vs Multica 架构对比

> 分析日期: 2026-04-20
> 参考资料: `docs/architecture.md`, `RESEARCH-multica-analysis.md`

---

## 架构对比分析

### 关键差异

| 维度 | Agent OS | Multica |
|------|----------|---------|
| **架构层次** | 图状态机 + 6 大自研模块 | Server → Daemon → Agent 三层 |
| **核心抽象** | ContextCompiler, MemoryService, ToolExecutor | Task Service, Poll Loop, Backend Interface |
| **记忆系统** | 四层记忆模型（Working/Session/Episodic/Semantic）| 无内置记忆，依赖 workdir 持久化 |
| **编排模式** | Flow/DAG/Loop/Cron + Meta Agent 动态选择 | 任务拉取 + Cron 定时触发 |
| **并发控制** | Semaphore + ConcurrencyController | Poll Loop + Semaphore 信号量 |
| **通信模型** | CommunicationBus（内存总线）| HTTP 轮询 + WebSocket 广播 |
| **Agent 定位** | 基础设施层（平台视角）| 产品层（把 Agent 当员工管）|
| **生命周期** | 消息级状态机 | Server 管理的任务状态流转 |
| **前端** | React Flow 可视化画布 | Next.js + 协作面板 |
| **蝴蝶模型** | 正向翼/反向翼双向联想 | 无 |

### 核心相似点

| 相似点 | 说明 |
|--------|------|
| **多 Agent 并发** | 都支持多 Agent 并行执行 |
| **任务优先级** | 都有任务优先级机制 |
| **事件总线** | Multica 同步 pub/sub，Agent OS 有 CommunicationBus |
| **生命周期管理** | 都关心 Agent/任务的生命周期 |
| **Cron 调度** | 都支持定时触发机制 |
| **WebSocket/SSE** | 都支持实时推送 |
| **React Flow** | 都用 React Flow 作为前端可视化层 |

### 两个系统的定位关系

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户 / 开发者                              │
└──────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┴────────────────────┐
         ▼                                         ▼
┌─────────────────────┐                   ┌─────────────────────┐
│      Multica        │                   │     Agent OS         │
│                     │                   │                     │
│  「谁来做这个任务」    │                   │  「任务内部如何执行」  │
│                     │                   │                     │
│  • 任务分配           │                   │  • Context 编译      │
│  • 员工管理           │                   │  • 记忆管理          │
│  • 进度追踪           │                   │  • 工具执行          │
│  • 实时协作           │                   │  • Agent 通信        │
│                     │                   │  • 并发控制          │
└─────────────────────┘                   └─────────────────────┘
         │                                         │
         │    ┌─────────────────────────────────────┘
         ▼    ▼
┌──────────────────────────────────────────────────────────────────┐
│                     实际执行层 (Claude Code 等)                      │
└──────────────────────────────────────────────────────────────────┘
```

**定位解读**：
- **Multica** 解决「**任务从哪里来、分配给谁、结果如何追踪**」— 工作流编排
- **Agent OS** 解决「**一个任务进来后，Agent 如何理解、分解、执行、记忆**」— 执行引擎编排
- 两者是**正交的**：Multica 负责「谁执行」，Agent OS 负责「如何执行」

---

## Mermaid 架构图

### 图 1: Agent OS 核心架构

```mermaid
flowchart TB
    subgraph USER["用户层"]
        UI["React Flow 画布<br/>(Next.js BFF)"]
    end

    subgraph GATEWAY["API Gateway"]
        HTTP["HTTP REST"]
        SSE["SSE 流"]
        WS["WebSocket"]
    end

    subgraph ORCHESTRATOR["Orchestrator 核心"]
        direction TB
        GC["Graph State Machine"]
        
        subgraph CORE["6 大自研模块"]
            CC["ContextCompiler<br/>组装上下文"]
            MS["MemoryService<br/>记忆管理"]
            TE["ToolExecutor<br/>工具执行"]
            CB["CommunicationBus<br/>Agent 通信"]
            CCt["ConcurrencyController<br/>并发控制"]
            KG["Knowledge Graph<br/>知识图谱"]
        end
        
        subgraph MEMORY["4 层记忆模型"]
            L0["L0: Working Memory<br/>(Context Window)"]
            L1["L1: Session Memory<br/>(SQLite)"]
            L2["L2: Episodic Memory<br/>(Vector DB)"]
            L3["L3: Semantic Memory<br/>(KG + Vector)"]
        end
    end

    subgraph SIDELINE["Side Agents"]
        KAIROS["Kairos<br/>时机感知"]
        VERIFIER["Verifier<br/>错误检测"]
        DREAMER["Dreamer<br/>记忆巩固"]
        CRITIC["Critic<br/>质量锚定"]
    end

    subgraph META["Meta Agent 机制"]
        SUBAGENT["Subagent<br/>(层级嵌套)"]
        TEAM["TeamAgent<br/>(Agent 间通信)"]
        SANDBOX["Sandbox<br/>(隔离执行)"]
        CONDITIONAL["Conditional Spawn<br/>(条件触发)"]
    end

    subgraph EXTERNAL["外部运行时"]
        CLAUDE["Claude Code"]
        CODEX["Codex"]
        OPENCLAW["OpenClaw"]
    end

    UI --> GATEWAY
    HTTP --> ORCHESTRATOR
    SSE --> ORCHESTRATOR
    WS --> ORCHESTRATOR

    GC --> CC
    GC --> MS
    GC --> TE
    GC --> CB
    GC --> CCt
    GC --> KG

    MS --> L0
    MS --> L1
    MS --> L2
    MS --> L3

    CC --> SIDELINE
    TE --> EXTERNAL

    META --> SUBAGENT
    META --> TEAM
    META --> SANDBOX
    META --> CONDITIONAL
```

### 图 2: Multica 核心架构

```mermaid
flowchart TB
    subgraph USER["用户层"]
        UI["Next.js 管理面板"]
        WS_CLIENT["WebSocket 客户端"]
    end

    subgraph SERVER["Server (Go)"]
        direction TB
        
        subgraph API["HTTP Handlers"]
            TASKS["Task Handler"]
            AUTOPILOT["Autopilot Handler"]
            AUTH["Auth Handler"]
        end

        subgraph SERVICE["Service Layer"]
            TASK_SVC["TaskService<br/>(入队/认领/完成)"]
            AUTO_SVC["AutopilotService<br/>(调度分发)"]
        end

        subgraph REALTIME["实时层"]
            HUB["WebSocket Hub<br/>(房间 + 广播)"]
            BUS["Event Bus<br/>(同步 pub/sub)"]
        end

        subgraph SCHEDULER["调度器"]
            AUTOPILOT_SCHED["Autopilot Scheduler<br/>(Cron 30s)"]
            TRIGGERS["Schedule Triggers"]
        end
    end

    subgraph DAEMON["Daemon (本地)"]
        direction TB
        
        POLL_LOOP["Poll Loop<br/>(轮询 + Semaphore)"]
        HANDLER["handleTask<br/>(任务执行)"]
        EXECENV["ExecEnv<br/>(工作目录管理)"]
        GC["Garbage Collector<br/>(清理过期目录)"]
        
        subgraph BACKENDS["Agent Backends"]
            CLAUDE["claude.go"]
            CODEX["codex.go"]
            OPENCLAW["openclaw.go"]
            HERMES["hermes.go"]
        end
    end

    subgraph DATABASE["PostgreSQL + pgvector"]
        TASKS_TABLE["agent_tasks"]
        AUTOPILOT_TABLE["autopilot_configs"]
        SCHEDULE_TABLE["schedule_triggers"]
        WORKSPACES["workspaces"]
    end

    UI --> TASKS
    UI --> AUTOPILOT
    WS_CLIENT --> HUB

    TASKS --> TASK_SVC
    AUTOPILOT --> AUTO_SVC

    TASK_SVC --> DATABASE
    AUTO_SVC --> DATABASE
    AUTOPILOT_SCHED --> TRIGGERS
    TRIGGERS --> AUTO_SVC

    HUB --> WS_CLIENT
    BUS --> HUB

    POLL_LOOP --> HANDLER
    HANDLER --> EXECENV
    HANDLER --> BACKENDS
    EXECENV --> GC

    DAEMON <--> SERVER
```

### 图 3: 架构层次对比

```mermaid
flowchart LR
    subgraph MULTICA["Multica"]
        direction TB
        M1["产品层<br/>Issue / Task / Chat"]
        M2["调度层<br/>Autopilot / Cron"]
        M3["执行层<br/>Daemon Poll Loop"]
        M4["运行时<br/>Claude / Codex / OpenClaw"]
    end

    subgraph AGENT_OS["Agent OS"]
        direction TB
        A1["呈现层<br/>React Flow Canvas"]
        A2["编排层<br/>Graph State Machine"]
        A3["执行层<br/>ToolExecutor + Guardrail"]
        A4["运行时<br/>LLM + Tools"]
    end

    M1 -. "分配任务" -.-> M3
    A2 -. "分解任务" -.-> A3
    M4 -. "实际执行" -.-> A4
```

---

### 图 4: 任务生命周期对比

```mermaid
sequenceDiagram
    participant U as User
    participant M as Multica Server
    participant D as Daemon
    participant A as Agent Runtime
    participant AO as Agent OS

    Note over U, M: Multica 任务生命周期
    U->>M: 创建 Issue
    M->>M: 入队 AgentTask (queued)
    D->>M: ClaimTask (轮询拉取)
    M-->>D: 返回待认领任务
    D->>D: StartTask (claimed → running)
    D->>A: 执行任务
    A-->>D: 执行结果
    D->>M: CompleteTask / FailTask

    Note over U, AO: Agent OS 任务执行
    U->>AO: 发送任务
    AO->>AO: ContextCompiler 编译上下文
    AO->>AO: MemoryService 召回相关记忆
    AO->>AO: ToolExecutor 执行工具调用
    AO->>AO: CommunicationBus 消息路由
    AO->>AO: 记忆写入 + 压缩
    AO-->>U: 返回结果
```

### 图 5: 记忆系统对比

```mermaid
mindmap
    root((记忆系统))
        Agent OS 记忆
            L0 Working Memory
                Context Window
                始终可见
                Compaction 压缩
            L1 Session Memory
                SQLite 持久化
                会话完整历史
            L2 Episodic Memory
                KG 为主(向量代码保留未启用)
                关键词 + KG unified 召回
                时间衰减
            L3 Semantic Memory
                Knowledge Graph
                实体关系
                用户画像
        Multica 记忆
            Workdir 持久化
                Git 分支保留
                会话恢复
                PriorSessionID
            无内置记忆系统
                依赖 Agent 自身
                依赖文件系统
            数据库只存储
                任务状态
                配置信息
```

---

### 图 6: 并发模型对比

```mermaid
flowchart TB
    subgraph MULTICA_CONCURRENCY["Multica 并发模型"]
        direction TB
        POLL["Poll Loop<br/>(每 N 秒)"]
        SEM["Semaphore<br/>(MaxConcurrentTasks)"]
        RUNTIMES["多个 Runtime<br/>(Round-robin)"]

        POLL --> SEM
        SEM --> RUNTIMES
        RUNTIMES -->|"for each runtime"| CLAIM["ClaimTask"]
        CLAIM -->|"并发执行"| HANDLE["handleTask<br/>(goroutine)"]
    end

    subgraph AGENT_OS_CONCURRENCY["Agent OS 并发模型"]
        direction TB
        CC["ConcurrencyController"]
        SEM_AO["Semaphore<br/>(per-agent / per-tool)"]

        CC --> SEM_AO
        SEM_AO -->|"Tool 级并发"| EXEC["ToolExecutor.execute()"]
        EXEC -->|"async/await"| GATHER["asyncio.gather()"]
    end
```

---

## 主流记忆系统对比（P4 补齐,2026-06-20）

> 引用记忆研究报告第七、八章结论。补充 hermes-agent / Letta / Mem0 / Zep 对比,凸显 agent-os-v2 差异化优势。

| 维度 | agent-os-v2 | hermes-agent | Letta | Mem0 | Zep |
|------|-------------|--------------|-------|------|-----|
| **记忆分层** | 四层(Working/Session/Episodic/Semantic)+ 状态机(P3) | provider 总线,无分层 | core memory blocks + archival | 事实/偏好自动提取 | 时间感知 graph + 向量 |
| **召回** | 关键词 + KG unified(向量代码保留未启用) | provider prefetch | 检索 + blocks | 向量 + LLM 提取 | Graphiti 时间图谱 |
| **巩固** | Dreamer(离线 L2→L3)+ TaskConsolidator(任务后在线)+ 状态机 | sync_all 后台单 worker | agent 自管理 | 自动 fact extraction | 自动 episodic → semantic |
| **产权边界** | origin(FOREGROUND/AGENT)保护用户记忆(P0) | skill_provenance | 无 | 无 | 无 |
| **事件解耦** | MemoryEventBus 6 钩子(P1) | MemoryManager 6 钩子 | 无 | 无 | 无 |
| **cache** | compiler 三层 + Anthropic cache_control(P2) | prompt_caching system_and_3 | 无 | 无 | 无 |

**agent-os-v2 四优势**(研究报告第八章 8.5,P0-P3 严守红线未推倒):🦋 蝴蝶翼双向联想 / 🛡️ 信任域 5 级权限 / 📊 五维评分(recency/frequency/relevance/emotional/actionable)/ 🔍 语义召回三模式。

## FAISS 真相校准（P4,2026-06-20）

文档早期(D-12/D-27)声明"已移除 FAISS",但代码(`vector.py` + `embedding.py`,all-MiniLM-L6-v2)仍存在。**真相:运行时未启用** —— `MemoryService` 传 `vector_store=None`,召回走关键词 + KG unified(`service.py` docstring 已校准)。FAISS 代码保留为 V2 pgvector 预留,不强启用(详见 TD-007)。

## 总结

| 维度 | Agent OS | Multica | 关系 |
|------|----------|---------|------|
| **职责** | Agent 执行引擎 | Agent 协作平台 | 正交互补 |
| **记忆** | 四层内存模型 | Workdir 持久化 | Agent OS 更系统化 |
| **编排** | Flow/DAG/Loop/Cron | Task Claim 拉取 | Agent OS 更灵活 |
| **扩展性** | 模块化自研 | Backend 接口抽象 | 两者都强调可扩展 |
| **适用场景** | 单 Agent 深度任务执行 | 多 Agent 协作管理 | 可以叠加使用 |

**关键洞察**：Multica 和 Agent OS 是**正交的**两个层次 — Multica 回答「**谁来执行**」，Agent OS 回答「**如何执行**」。未来可能的设计是：Multica 作为任务管理层，调用 Agent OS 作为执行引擎。
