# Agent OS 与 Multica：编排维度正交性分析

> 分析日期: 2026-04-20
> 参考资料: `docs/architecture.md`, `RESEARCH-multica-analysis.md`

---

## 核心论点

**Multica 管「谁执行」，Agent OS 管「如何执行」** — 两者在编排维度上完全正交，可以叠加使用。

---

## 正交性图解

```mermaid
flowchart TB
    subgraph ORTH["正交坐标系"]
        direction TB
        X_AXIS["X 轴：任务分配<br/>(Who - 谁来执行)"]
        Y_AXIS["Y 轴：任务执行<br/>(How - 如何执行)"]
    end

    subgraph QUADRANTS["象限"]
        direction TB
        Q1["❌ 无效区域<br/>(不知道谁执行<br/>也不知道如何执行)"]
        Q2["✅ Multica 区域<br/>(知道谁执行<br/>但不关心如何执行)"]
        Q3["✅ Agent OS 区域<br/>(不关心谁执行<br/>但深度解决如何执行)"]
        Q4["⭐ 叠加区域<br/>(知道谁执行<br/>+ 深度如何执行)"]
    end

    ORTH --> QUADRANTS

    X_AXIS -. "Multica 负责" .-> Q2
    X_AXIS -. "Multica 负责" .-> Q4
    Y_AXIS -. "Agent OS 负责" .-> Q3
    Y_AXIS -. "Agent OS 负责" .-> Q4
```

---

## 「谁执行」vs「如何执行」分离

### Multica 回答「谁执行」

```mermaid
flowchart LR
    subgraph WHO["谁执行？"]
        ISSUE["Issue 创建"]
        TASK_QUEUE["任务队列"]
        RUNTIMES["多个 Runtime"]
        CLAIM["谁抢到了？"]
        EXECUTE["执行者身份"]
    end

    ISSUE --> TASK_QUEUE
    TASK_QUEUE --> RUNTIMES
    RUNTIMES --> CLAIM
    CLAIM --> EXECUTE
```

**Multica 的职责**：
1. 接收 Issue / Chat / Cron 触发
2. 入队到任务队列
3. 多个 Runtime（Claude Code / Codex / OpenClaw 等）竞争认领
4. 分配执行者身份

**关键问题**：「这个任务分配给哪个 Agent？它的状态如何？」

---

### Agent OS 回答「如何执行」

```mermaid
flowchart LR
    subgraph HOW["如何执行？"]
        CONTEXT["Context 编译"]
        MEMORY["记忆召回"]
        TOOLS["工具调用"]
        DECOMPOSE["任务分解"]
        LOOP["循环迭代"]
        RESULT["结果输出"]
    end

    CONTEXT --> MEMORY
    MEMORY --> TOOLS
    TOOLS --> DECOMPOSE
    DECOMPOSE --> LOOP
    LOOP -->|"收敛?"| RESULT
    LOOP -. "继续迭代" .-> DECOMPOSE
```

**Agent OS 的职责**：
1. 理解任务意图（ContextCompiler）
2. 召回相关记忆（MemoryService）
3. 分解为可执行步骤（Graph State Machine）
4. 调用工具执行（ToolExecutor）
5. 迭代直到收敛（Loop Orchestrator）
6. 记忆写入（MemoryService）

**关键问题**：「任务进来后，Agent 如何理解、分解、执行、记忆？」

---

## 叠加架构

当 Multica 和 Agent OS 叠加时：

```mermaid
flowchart TB
    subgraph COMBINED["叠加架构"]
        direction TB

        subgraph MULTICA_LAYER["Multica 层（谁执行）"]
            ISSUE["Issue 入队"]
            QUEUE["任务队列"]
            CLAIM["Runtime 竞争认领"]
            ASSIGN["分配执行者"]
        end

        subgraph BRIDGE["Bridge（协议层）"]
            PROTOCOL["Multica Task Protocol<br/>→ Agent OS Task Format"]
            WORKDIR["工作目录传递"]
            SESSION["会话 ID 传递"]
        end

        subgraph AGENT_OS_LAYER["Agent OS 层（如何执行）"]
            CC["ContextCompiler"]
            MS["MemoryService"]
            TE["ToolExecutor"]
            CB["CommunicationBus"]
            LOOP["Loop Orchestrator"]
        end

        subgraph RUNTIME["实际运行时"]
            CLAUDE["Claude Code"]
            CODEX["Codex"]
        end
    end

    USER["用户"] --> MULTICA_LAYER
    MULTICA_LAYER --> BRIDGE
    BRIDGE --> AGENT_OS_LAYER
    AGENT_OS_LAYER --> RUNTIME

    QUEUE -. "Workdir 复用" .-> WORKDIR
    ASSIGN -. "PriorSessionID" .-> SESSION
```

### 叠加流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant M as Multica
    participant Q as 任务队列
    participant D as Daemon
    participant A as Agent OS
    participant R as Claude Code

    U->>M: 创建 Issue
    M->>Q: 入队任务

    Note over D: Multica Poll Loop
    D->>Q: ClaimTask (竞争)
    Q-->>D: 返回任务 + Workdir
    D->>D: 准备执行环境

    Note over A: Agent OS 接管执行
    D->>A: 传递 Task + Workdir + Session
    A->>A: ContextCompiler 编译
    A->>A: MemoryService 召回
    A->>A: 分解任务为 Graph Node
    A->>A: ToolExecutor 执行工具

    loop Loop Orchestrator
        A->>R: 调用工具
        R-->>A: 工具结果
        A->>A: 评估收敛条件
        alt 未收敛
            A->>A: 继续下一轮
        end
    end

    A->>D: 返回结果
    D->>M: CompleteTask
    M-->>U: 任务完成通知
```

---

## 职责边界清晰划分

```mermaid
flowchart TB
    subgraph BOUNDARY["职责边界"]
        direction TB

        subgraph MULTICA_BOUNDARY["Multica 边界"]
            M1["任务来源管理<br/>(Issue / Chat / Cron)"]
            M2["任务队列<br/>(入队 / 认领 / 状态)"]
            M3["Runtime 管理<br/>(多 Agent 调度)"]
            M4["进度追踪<br/>(WebSocket 广播)"]
            M5["生命周期<br/>(claimed/running/completed)"]
        end

        subgraph INTERFACE["接口协议"]
            I1["Task 格式定义"]
            I2["Workdir 传递"]
            I3["Session 恢复"]
            I4["结果上报"]
        end

        subgraph AGENT_OS_BOUNDARY["Agent OS 边界"]
            A1["意图理解<br/>(ContextCompiler)"]
            A2["记忆召回<br/>(MemoryService)"]
            A3["任务分解<br/>(Graph State Machine)"]
            A4["工具执行<br/>(ToolExecutor)"]
            A5["循环迭代<br/>(Loop/DAG/Flow/Cron)"]
            A6["并发控制<br/>(ConcurrencyController)"]
        end
    end

    MULTICA_BOUNDARY --> INTERFACE
    INTERFACE --> AGENT_OS_BOUNDARY
```

| 职责 | Multica | Agent OS |
|------|---------|----------|
| 任务来源 | ✅ | ❌ |
| 任务队列 | ✅ | ❌ |
| Agent 调度 | ✅ | ❌ |
| 进度追踪 | ✅ | ❌ |
| 意图理解 | ❌ | ✅ |
| 记忆管理 | ❌ | ✅ |
| 任务分解 | ❌ | ✅ |
| 工具执行 | ❌ | ✅ |
| 循环迭代 | ❌ | ✅ |
| 并发控制 | ❌ | ✅ |

---

## 正交性验证

### 维度 1: 问题空间正交

- **Multica** 在**组织维度**提问：在多 Agent 系统中，任务如何分配？
- **Agent OS** 在**单 Agent 维度**提问：在单个 Agent 内部，任务如何执行？

### 维度 2: 抽象层次正交

- **Multica** 是**产品层抽象**：任务 = Issue / Task / 完成状态
- **Agent OS** 是**引擎层抽象**：任务 = Context + Memory + Tool Call Graph

### 维度 3: 可独立演进

- **Multica** 可以使用任何「如何执行」的引擎（不仅是 Agent OS）
- **Agent OS** 可以接受任何「谁来执行」的输入（不仅是 Multica）

---

## 设计启示

### 当前状态

```
Multica 单独使用：任务管理强大，但执行深度依赖 Agent 自身能力
Agent OS 单独使用：执行引擎强大，但任务来源需要自己实现
```

### 理想状态

```
Multica（任务管理层）+ Agent OS（执行引擎层）= 完整的 Agent 工作流平台
```

### 具体借鉴

| 借鉴点 | 来源 | 应用到 Agent OS |
|--------|------|----------------|
| Workdir 复用 | Multica | Agent 级别上下文持久化 |
| Session 恢复 | Multica | PriorSessionID 机制 |
| Backend 接口抽象 | Multica | 统一 LLM Provider 接口 |
| Autopilot 两种模式 | Multica | 任务是否走完整 Issue 链路 |

---

## 结论

**Multica 和 Agent OS 是正交的两个编排维度**：

1. **Multica** = 任务管理层（谁执行）
2. **Agent OS** = 执行引擎层（如何执行）
3. 两者叠加 = 完整的 Agent 工作流平台

这不是竞争关系，而是**互补关系**。未来 Agent OS 可以作为 Multica 的执行引擎后端，同时保留自己的完整功能。
