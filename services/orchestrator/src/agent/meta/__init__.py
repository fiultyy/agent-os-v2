"""Agent meta module — Meta Agent 机制抽象。

包含：
- ConditionalSpawner: 条件型任务生成器
- SandboxExecutor: 隔离进程执行器
- MetaAgentNode: Graph State Machine 节点类型

参考：
- OpenClaw cron spawning (tools/openclaw/src/infra/heartbeat-runner.ts)
- OpenClaw spawn 机制 (tools/openclaw/src/acp/control-plane/spawn.ts)
"""

from src.agent.meta.conditional_spawner import (
    ConditionalSpawner,
    SpawnConfig,
    TriggerType,
)
from src.agent.meta.sandbox import SandboxExecutor, SandboxResult
from src.agent.meta.meta_agent_node import MetaAgentNode

__all__ = [
    "ConditionalSpawner",
    "SpawnConfig",
    "TriggerType",
    "SandboxExecutor",
    "SandboxResult",
    "MetaAgentNode",
]
