"""ConcurrencyController — manages parallel execution of agents and tools.

Handles:
- Concurrent tool execution with configurable limits
- Agent execution semaphore (max parallel agents)
- Priority-based task scheduling
- Resource contention resolution
"""

import asyncio
import heapq
import uuid
from dataclasses import dataclass, field
from typing import Any, Coroutine
from enum import IntEnum


class Priority(IntEnum):
    """Task priority levels — lower value = higher priority."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass(order=True)
class _PrioritizedTask:
    """Wrapper for priority-queue scheduling."""

    sort_key: tuple[int, int] = field(compare=True)  # (priority, sequence)
    task_id: str = field(compare=False)
    coro: Coroutine[Any, Any, Any] = field(compare=False)


class ConcurrencyController:
    """Controls concurrent execution of agents and tools.

    Uses semaphores to bound parallelism and a priority queue
    for task scheduling.
    """

    def __init__(self, max_agents: int = 10, max_tools: int = 20):
        self.max_agents = max_agents
        self.max_tools = max_tools
        self._agent_semaphore = asyncio.Semaphore(max_agents)
        self._tool_semaphore = asyncio.Semaphore(max_tools)
        # Track which agents/tools currently hold slots
        self._active_agents: dict[str, asyncio.Semaphore] = {}
        self._active_tools: dict[str, set[str]] = {}
        # Priority scheduling
        self._queue: list[_PrioritizedTask] = []
        self._seq = 0
        self._running = False
        self._drain_event: asyncio.Event | None = None

    # ── Agent slot management ─────────────────────────────────────

    async def acquire_agent_slot(self, agent_id: str) -> bool:
        """Acquire an execution slot for an agent.

        Blocks until a slot is available under the semaphore limit.
        Returns ``True`` on success.
        """
        await self._agent_semaphore.acquire()
        self._active_agents[agent_id] = self._agent_semaphore
        return True

    async def release_agent_slot(self, agent_id: str) -> None:
        """Release an agent execution slot."""
        if agent_id in self._active_agents:
            del self._active_agents[agent_id]
            self._agent_semaphore.release()

    # ── Tool slot management ──────────────────────────────────────

    async def acquire_tool_slot(self, tool_name: str) -> bool:
        """Acquire a tool execution slot.

        Respects the global tool concurrency limit.
        Returns ``True`` on success.
        """
        await self._tool_semaphore.acquire()
        self._active_tools.setdefault(tool_name, set()).add(str(uuid.uuid4()))
        return True

    async def release_tool_slot(self, tool_name: str) -> None:
        """Release a tool execution slot."""
        active = self._active_tools.get(tool_name)
        if active:
            active.pop()
            if not active:
                del self._active_tools[tool_name]
        self._tool_semaphore.release()

    # ── Priority scheduling ───────────────────────────────────────

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        priority: Priority = Priority.NORMAL,
        task_id: str = "",
    ) -> str:
        """Submit a coroutine for scheduled execution.

        Args:
            coro: The coroutine to schedule.
            priority: Scheduling priority.
            task_id: Optional identifier; auto-generated if empty.

        Returns:
            The task ID.
        """
        if not task_id:
            task_id = str(uuid.uuid4())
        self._seq += 1
        item = _PrioritizedTask(
            sort_key=(priority, self._seq),
            task_id=task_id,
            coro=coro,
        )
        heapq.heappush(self._queue, item)
        return task_id

    async def run_pending(self, max_concurrent: int | None = None) -> list[Any]:
        """Execute all pending tasks respecting priority and concurrency.

        Args:
            max_concurrent: Override for max parallel tasks (defaults to max_agents).

        Returns:
            List of results from completed tasks.
        """
        limit = max_concurrent or self.max_agents
        semaphore = asyncio.Semaphore(limit)
        results: list[Any] = []
        tasks: list[asyncio.Task[Any]] = []

        async def _execute(ptask: _PrioritizedTask) -> Any:
            async with semaphore:
                return await ptask.coro

        while self._queue:
            ptask = heapq.heappop(self._queue)
            tasks.append(asyncio.create_task(_execute(ptask)))

        if tasks:
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for item in done:
                results.append(item)

        return results

    @property
    def queue_size(self) -> int:
        """Number of tasks waiting in the priority queue."""
        return len(self._queue)

    @property
    def active_agent_count(self) -> int:
        """Number of agents currently holding execution slots."""
        return len(self._active_agents)

    @property
    def active_tool_count(self) -> int:
        """Total number of active tool slots across all tools."""
        return sum(len(s) for s in self._active_tools.values())
