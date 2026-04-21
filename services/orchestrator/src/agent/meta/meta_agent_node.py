"""MetaAgentNode — 与 Graph State Machine 集成的节点类型。

MetaAgentNode 是 GraphNode 的子类，代表一个可嵌套的 subagent。
通过与 ConditionalSpawner 配合，实现条件触发型子 Agent 执行。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.graph.nodes import GraphNode
from src.graph.state import GraphState

if TYPE_CHECKING:
    from src.agent.meta.conditional_spawner import ConditionalSpawner, SpawnConfig

logger = logging.getLogger(__name__)


class MetaAgentNode(GraphNode):
    """Graph 节点，代表一个 subagent。

    MetaAgentNode 是 GraphNode 的子类，代表一个可嵌套的 subagent。
    它通过 ConditionalSpawner 创建子 Agent，并在 Graph State Machine 中
    作为普通节点参与执行流程。

    状态流转：
    - pending → running → completed / failed

    Attributes:
        agent_id: Subagent 的唯一标识。
        spawn_config: 创建该 agent 的配置。
        status: 当前状态（pending/running/completed/failed）。
        result: 执行结果。
        spawner: 关联的 ConditionalSpawner（可选）。
    """

    def __init__(
        self,
        name: str,
        agent_id: str | None = None,
        spawn_config: "SpawnConfig | None" = None,
        spawner: "ConditionalSpawner | None" = None,
    ) -> None:
        """初始化 MetaAgentNode。

        Args:
            name: 节点名称。
            agent_id: Subagent 的 ID（若已存在）。
            spawn_config: 创建 subagent 的配置。
            spawner: ConditionalSpawner 实例。
        """
        super().__init__(name)
        self.agent_id = agent_id or ""
        self.spawn_config = spawn_config
        self.spawner = spawner
        self.status = "pending"  # pending / running / completed / failed
        self.result: dict[str, Any] | None = None
        self._execution_task: asyncio.Task | None = None
        self._cancel_event: asyncio.Event | None = None

    async def execute(self, state: GraphState) -> GraphState:
        """Execute meta agent and update state.

        Creates a background task for agent execution.
        Subclasses should override _execute_agent() for real integration.

        Args:
            state: Current GraphState.

        Returns:
            Updated GraphState.
        """
        self._cancel_event = asyncio.Event()
        self.status = "running"
        state.current_node = self.name

        try:
            # 如果没有 agent_id，尝试创建
            if not self.agent_id and self.spawner and self.spawn_config:
                agent_id = await self._spawn_agent(state)
                if agent_id:
                    self.agent_id = agent_id
                    logger.info(f"MetaAgentNode '{self.name}' spawned agent {agent_id}")

            if not self.agent_id:
                state.errors.append(f"MetaAgentNode '{self.name}': No agent_id available")
                self.status = "failed"
                return state

            # 创建后台任务执行 agent（真正的任务对象，供 cancel() 使用）
            self._execution_task = asyncio.create_task(
                self._execute_agent(state)
            )

            # 等待执行完成（或超时）
            try:
                self.result = await asyncio.wait_for(
                    self._execution_task,
                    timeout=300,
                )
                self.status = "completed"
            except asyncio.TimeoutError:
                self.status = "completed"  # timeout != failure
                self.result = {
                    **(self.result or {}),
                    "timeout": True,
                }

            # 更新 state
            state.subgraph_results[self.name] = self.result
            state.metadata[f"{self.name}_status"] = self.status
            logger.info(f"MetaAgentNode '{self.name}' completed with status {self.status}")

        except asyncio.CancelledError:
            self.status = "failed"
            state.errors.append(f"MetaAgentNode '{self.name}' was cancelled")
            logger.warning(f"MetaAgentNode '{self.name}' was cancelled")
            raise
        except Exception as e:
            self.status = "failed"
            error_msg = f"MetaAgentNode '{self.name}' error: {e}"
            state.errors.append(error_msg)
            logger.exception(error_msg)
        finally:
            self._cancel_event = None
            self._execution_task = None

        return state

    async def _spawn_agent(self, state: GraphState) -> str | None:
        """通过 spawner 创建 subagent。"""
        if not self.spawner or not self.spawn_config:
            return None

        context: dict[str, Any] = {
            "event": state.context.get("event", ""),
        }
        return await self.spawner.spawn(
            trigger=self.spawn_config.trigger,
            context=context,
        )

    async def _execute_agent(self, state: GraphState) -> dict[str, Any]:
        """Execute subagent and return result.

        This is a stub that integrates with the agent_manager.
        Override this method for real agent execution.
        The actual implementation should:
        1. Call agent_manager to run the subagent with agent_id
        2. Stream results back via a queue or callback
        3. Check self._cancel_event periodically for cancellation
        """
        if self.spawner and self.agent_id:
            # 定期检查取消信号（每 5 秒）
            for _ in range(60):  # 最多 5 分钟
                if self._cancel_event and self._cancel_event.is_set():
                    self.status = "failed"
                    return {"error": "Cancelled", "agent_id": self.agent_id}
                await asyncio.sleep(5)

        # 构建结果（stub）
        result = {
            "agent_id": self.agent_id,
            "status": self.status,
            "node_name": self.name,
            "input": state.input,
            "messages_count": len(state.messages),
        }
        self.status = "completed"
        return result

    async def cancel(self) -> None:
        """Cancel execution."""
        logger.info(f"Cancelling MetaAgentNode '{self.name}' (agent_id={self.agent_id})")
        if self.status in ("running", "executing"):
            self.status = "failed"

        if self._cancel_event:
            self._cancel_event.set()

        if self._execution_task and not self._execution_task.done():
            self._execution_task.cancel()
            try:
                await self._execution_task
            except asyncio.CancelledError:
                pass

    def get_status(self) -> dict[str, Any]:
        """返回当前状态快照。"""
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "status": self.status,
            "result": self.result,
        }

    def __repr__(self) -> str:
        return (
            f"MetaAgentNode(name={self.name!r}, agent_id={self.agent_id!r}, "
            f"status={self.status!r})"
        )
