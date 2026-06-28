"""ConcurrencyController — manages parallel execution of agents and tools.

Handles:
- Concurrent tool execution with configurable limits
- Agent execution semaphore (max parallel agents)
- Priority-based task scheduling with heap queue
- Timeout and cancellation for running tasks
- Resource contention resolution
- Dependency-aware execution: detect tool parameter dependencies
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Task priority levels — lower value = higher priority."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class TaskInfo:
    """Metadata about a scheduled or running task."""

    task_id: str
    priority: Priority
    status: str = "pending"  # pending | running | done | failed | cancelled
    result: Any = None
    error: str | None = None
    depends_on: list[str] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(order=True)
class _PrioritizedTask:
    """Wrapper for priority-queue scheduling."""

    sort_key: tuple[int, int] = field(compare=True)  # (priority, sequence)
    task_id: str = field(compare=False)
    coro: Coroutine[Any, Any, Any] = field(compare=False)


class ConcurrencyController:
    """Controls concurrent execution of agents and tools.

    Uses semaphores to bound parallelism, a priority queue
    for task scheduling, and per-task cancellation events.

    NOT-WIRED (deferred): 8 个 tool-slot/cancellation/dependency 子能力 API
    (acquire_tool_slot/release_tool_slot/cancel_all/is_cancelled/
    execute_with_timeout/add_dependency/get_task_info/get_ready_tasks)零引用。
    类本身活跃(chat.py acquire_agent_slot + orchestrate.py),这些未通电能力保留
    以便未来并发扩展,勿删。
    """

    def __init__(self, max_agents: int = 10, max_tools: int = 20) -> None:
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
        # Task tracking
        self._task_info: dict[str, TaskInfo] = {}
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        # Dependency tracking: task_id -> set of task_ids it depends on
        self._dependencies: dict[str, set[str]] = {}
        # Completed task results for dependency resolution
        self._completed: dict[str, Any] = {}

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

    async def acquire_tool_slot(self, tool_name: str) -> str:
        """Acquire a tool execution slot.

        Respects the global tool concurrency limit.
        Returns a slot ID for tracking.
        """
        await self._tool_semaphore.acquire()
        slot_id = str(uuid.uuid4())
        self._active_tools.setdefault(tool_name, set()).add(slot_id)
        return slot_id

    async def release_tool_slot(self, tool_name: str, slot_id: str) -> None:
        """Release a tool execution slot by slot ID."""
        active = self._active_tools.get(tool_name)
        if active and slot_id in active:
            active.discard(slot_id)
            if not active:
                del self._active_tools[tool_name]
        self._tool_semaphore.release()

    # ── Priority scheduling ───────────────────────────────────────

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        priority: Priority = Priority.NORMAL,
        task_id: str = "",
        depends_on: list[str] | None = None,
    ) -> str:
        """Submit a coroutine for scheduled execution.

        Args:
            coro: The coroutine to schedule.
            priority: Scheduling priority.
            task_id: Optional identifier; auto-generated if empty.
            depends_on: List of task IDs that must complete first.

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

        # Track task metadata
        info = TaskInfo(task_id=task_id, priority=priority)
        if depends_on:
            info.depends_on = list(depends_on)
            self._dependencies[task_id] = set(depends_on)
        self._task_info[task_id] = info

        return task_id

    async def run_pending(self, max_concurrent: int | None = None) -> list[Any]:
        """Execute all pending tasks respecting priority and concurrency.

        Tasks with unmet dependencies are deferred until their
        prerequisites complete.

        Args:
            max_concurrent: Override for max parallel tasks (defaults to max_agents).

        Returns:
            List of results from completed tasks.
        """
        import heapq as heapq_mod

        limit = max_concurrent or self.max_agents
        semaphore = asyncio.Semaphore(limit)
        results: list[Any] = []
        tasks: list[asyncio.Task[Any]] = []
        deferred: list[_PrioritizedTask] = []

        async def _execute(ptask: _PrioritizedTask) -> Any:
            async with semaphore:
                info = self._task_info.get(ptask.task_id)
                if info:
                    info.status = "running"
                    # Set up cancellation check
                    info.cancel_event.clear()

                try:
                    result = await ptask.coro
                    if info:
                        info.status = "done"
                        info.result = result
                    self._completed[ptask.task_id] = result
                    return result
                except asyncio.CancelledError:
                    if info:
                        info.status = "cancelled"
                    raise
                except Exception as exc:
                    if info:
                        info.status = "failed"
                        info.error = str(exc)
                    raise

        # Process queue respecting dependencies
        while self._queue:
            ptask = heapq_mod.heappop(self._queue)

            # Check dependencies
            deps = self._dependencies.get(ptask.task_id, set())
            unmet = {d for d in deps if d not in self._completed}
            if unmet:
                # Defer — will retry after current batch completes
                deferred.append(ptask)
                continue

            tasks.append(asyncio.create_task(_execute(ptask)))

        if tasks:
            done = await asyncio.gather(*tasks, return_exceptions=True)
            for item in done:
                if isinstance(item, Exception):
                    results.append(item)
                else:
                    results.append(item)

        # Re-queue deferred tasks if any completed their dependencies
        if deferred:
            still_deferred: list[_PrioritizedTask] = []
            for ptask in deferred:
                deps = self._dependencies.get(ptask.task_id, set())
                unmet = {d for d in deps if d not in self._completed}
                if unmet:
                    still_deferred.append(ptask)
                else:
                    heapq.heappush(self._queue, ptask)

            if still_deferred:
                self._queue.extend(still_deferred)
                heapq.heapify(self._queue)

            if self._queue:
                more_results = await self.run_pending(max_concurrent)
                results.extend(more_results)

        return results

    # ── Task cancellation ─────────────────────────────────────────

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or running task.

        Args:
            task_id: The task to cancel.

        Returns:
            True if the task was successfully cancelled.
        """
        info = self._task_info.get(task_id)
        if info is None:
            return False

        # Signal cancellation via event
        info.cancel_event.set()

        # Cancel running asyncio.Task if tracked
        running = self._running_tasks.get(task_id)
        if running and not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            info.status = "cancelled"
            return True

        # Remove from queue if pending
        self._queue = [t for t in self._queue if t.task_id != task_id]
        if info.status == "pending":
            info.status = "cancelled"
            return True

        return False

    async def cancel_all(self) -> int:
        """Cancel all pending and running tasks.

        Returns:
            Number of tasks cancelled.
        """
        cancelled = 0
        for task_id in list(self._task_info.keys()):
            if await self.cancel_task(task_id):
                cancelled += 1
        return cancelled

    def is_cancelled(self, task_id: str) -> bool:
        """Check if a task has been cancelled."""
        info = self._task_info.get(task_id)
        if info is None:
            return False
        return info.status == "cancelled" or info.cancel_event.is_set()

    # ── Timeout execution ─────────────────────────────────────────

    async def execute_with_timeout(
        self,
        coro: Coroutine[Any, Any, Any],
        timeout: float,
        task_id: str = "",
        priority: Priority = Priority.NORMAL,
    ) -> Any:
        """Execute a coroutine with a timeout and automatic cancellation.

        Args:
            coro: Coroutine to execute.
            timeout: Maximum seconds to wait.
            task_id: Optional task identifier.
            priority: Scheduling priority.

        Returns:
            The coroutine result.

        Raises:
            asyncio.TimeoutError: If execution exceeds timeout.
        """
        if not task_id:
            task_id = str(uuid.uuid4())

        info = TaskInfo(task_id=task_id, priority=priority, status="running")
        self._task_info[task_id] = info

        task = asyncio.create_task(coro)
        self._running_tasks[task_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=timeout)
            info.status = "done"
            info.result = result
            self._completed[task_id] = result
            return result
        except asyncio.TimeoutError:
            task.cancel()
            info.status = "cancelled"
            info.error = f"Timeout after {timeout}s"
            raise
        except Exception as exc:
            info.status = "failed"
            info.error = str(exc)
            raise
        finally:
            self._running_tasks.pop(task_id, None)

    # ── Dependency management ─────────────────────────────────────

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Declare that *task_id* depends on *depends_on* completing first."""
        self._dependencies.setdefault(task_id, set()).add(depends_on)
        info = self._task_info.get(task_id)
        if info and depends_on not in info.depends_on:
            info.depends_on.append(depends_on)

    def get_task_info(self, task_id: str) -> TaskInfo | None:
        """Get metadata about a tracked task."""
        return self._task_info.get(task_id)

    def get_ready_tasks(self) -> list[str]:
        """Return task IDs whose dependencies are all satisfied."""
        ready: list[str] = []
        for task_id, deps in self._dependencies.items():
            info = self._task_info.get(task_id)
            if info and info.status != "pending":
                continue
            unmet = {d for d in deps if d not in self._completed}
            if not unmet:
                ready.append(task_id)
        return ready

    # ── Status ────────────────────────────────────────────────────

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

    def get_status(self) -> dict[str, Any]:
        """Return a summary of the controller state."""
        return {
            "queue_size": self.queue_size,
            "active_agents": self.active_agent_count,
            "active_tools": self.active_tool_count,
            "max_agents": self.max_agents,
            "max_tools": self.max_tools,
            "total_tasks": len(self._task_info),
            "completed_tasks": len(self._completed),
        }
