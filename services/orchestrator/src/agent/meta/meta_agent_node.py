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
        agent_manager: Any = None,
        session_id: str = "",
    ) -> None:
        """初始化 MetaAgentNode。

        Args:
            name: 节点名称。
            agent_id: Subagent 的 ID（若已存在）。
            spawn_config: 创建 subagent 的配置。
            spawner: ConditionalSpawner 实例。
            agent_manager: Agent 管理器实例，需提供 ``create_subagent`` /
                ``teardown_subagent`` 接口(P0 契约)。当为 ``None`` 或缺少这两个
                方法时，``execute()`` 回退到 *stub 兼容路径*(``_build_result``
                空壳),不破坏旧的 mock 测试。
            session_id: 会话 id，传给 create_subagent / run_agent_turn。
        """
        super().__init__(name)
        self.agent_id = agent_id or ""
        self.spawn_config = spawn_config
        self.spawner = spawner
        self._agent_manager = agent_manager
        self._session_id = session_id
        self.status = "pending"  # pending / running / completed / failed
        self.result: dict[str, Any] | None = None
        self._execution_task: asyncio.Task | None = None
        self._cancel_event: asyncio.Event | None = None
        self._completion_event: asyncio.Event | None = None

    def _has_real_lifecycle(self) -> bool:
        """真模式可用性探测:agent_manager 同时具备 create_subagent +
        teardown_subagent。

        - True  → ``_execute_agent`` 走真闭环(spawn→run→teardown),
                  completion_event 由真完成 set。
        - False → stub 兼容路径(空壳 _build_result),保持旧行为。
        """
        am = self._agent_manager
        return (
            am is not None
            and hasattr(am, "create_subagent")
            and hasattr(am, "teardown_subagent")
        )

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
        self._completion_event = asyncio.Event()
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

            # completion_event semantics:
            # - STUB mode (no real agent_manager): pre-set now so the event-driven
            #   wait in _execute_agent's legacy loop returns immediately. This
            #   preserves the historical "instant completion" behaviour for old
            #   mocks / direct-constructed nodes.
            # - REAL mode (create_subagent + teardown_subagent present): do NOT
            #   pre-set — _execute_agent runs the actual spawn→run→teardown loop
            #   and is responsible for setting completion_event on real finish.
            #   Eliminates the stub "假阳性" (false success before any LLM call).
            if not self._has_real_lifecycle():
                self._completion_event.set()

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
        """Execute subagent and wait for completion.

        Two execution modes:

        - **REAL mode** (``self._agent_manager`` exposes ``create_subagent`` and
          ``teardown_subagent``): runs the closed loop
          ``create_subagent → run_agent_turn → teardown_subagent``. The result
          of :func:`run_agent_turn` (the LLM response string) is written into the
          returned dict under ``response``. ``self._completion_event`` is set
          *after* the real turn completes (not pre-set by ``execute()``),
          eliminating the stub false-success. Memory red-line R1 is honoured by
          ``run_agent_turn`` (zero memory calls).

        - **STUB mode** (no agent_manager, or missing lifecycle methods):
          preserves the historical behaviour — an event-driven wait that returns
          the empty ``_build_result`` shell. This keeps old mock-based tests and
          direct-constructed nodes working unchanged.
        """
        # ── REAL mode: spawn → run → teardown closed loop ─────────────────────
        if self._has_real_lifecycle():
            return await self._run_real_turn(state)

        # ── STUB mode: legacy event-driven wait (compatibility) ───────────────
        # If events were never initialized (direct call, not via execute()),
        # return immediately for backward compatibility.
        if self._completion_event is None and self._cancel_event is None:
            return self._build_result(state, "completed")

        timeout = 300
        check_interval = 1

        for _ in range(timeout):
            if self._cancel_event and self._cancel_event.is_set():
                return {"error": "Cancelled", "agent_id": self.agent_id}

            if self._completion_event and self._completion_event.is_set():
                return self._build_result(state, "completed")

            await asyncio.sleep(check_interval)

        return self._build_result(state, "timeout")

    async def _run_real_turn(self, state: GraphState) -> dict[str, Any]:
        """Real closed loop: create_subagent → run_agent_turn → teardown_subagent.

        Honours the closed-loop contract of P1:

        1. ``create_subagent(agent_type, config, parent_id, session_id)`` →
           ``{"id": sub_id}`` (P0 contract, locked by conditional_spawner).
        2. ``run_agent_turn(sub_id, input, session_id, system_prompt)`` → the
           subagent's LLM response string. Memory-free (R1), context-isolated.
        3. ``teardown_subagent(sub_id)`` → always invoked (best-effort, in
           ``finally``) so a transient subagent never leaks.

        ``self._completion_event`` is set on real completion (success *or*
        controlled failure), so the stub-mode pre-set in ``execute()`` is not
        relied upon.
        """
        # Local import keeps the stub path free of the (heavier) agent_runner
        # dependency graph — old mocks / direct-constructed nodes that never hit
        # real mode pay nothing.
        from src.agent.meta.agent_runner import run_agent_turn

        agent_type = ""
        spawn_config_dict: dict[str, Any] = {}
        system_prompt: str | None = None
        if self.spawn_config is not None:
            agent_type = getattr(self.spawn_config, "agent_type", "") or ""
            spawn_config_dict = getattr(self.spawn_config, "config", {}) or {}
            system_prompt = spawn_config_dict.get("system_prompt")

        # The input fed to the subagent: prefer the node's GraphState input,
        # fall back to the configured payload, then to an empty string.
        turn_input = state.input or spawn_config_dict.get("input", "") or ""

        sub_id: str | None = None
        response: str = ""
        error: str | None = None
        session_id = self._session_id or state.session_id

        try:
            spawn_result = await self._agent_manager.create_subagent(
                agent_type=agent_type or self.name,
                config=spawn_config_dict,
                parent_id=self.agent_id or "",
                session_id=session_id,
            )
            raw_id = spawn_result.get("id") if isinstance(spawn_result, dict) else None
            # Treat empty/whitespace ids as "no id" (None) so the run is skipped
            # AND teardown is not invoked with a bogus empty string.
            sub_id = (raw_id or None) if (isinstance(raw_id, str) and raw_id.strip()) else None

            if not sub_id:
                error = "create_subagent returned no id"
            else:
                response = await run_agent_turn(
                    agent_id=sub_id,
                    input=turn_input,
                    session_id=session_id,
                    system_prompt=system_prompt,
                )
        except asyncio.CancelledError:
            # Propagate cancellation but still attempt teardown below.
            error = "Cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — degrade, record, do not abort graph
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("MetaAgentNode '%s' real turn failed", self.name)
        finally:
            # Teardown is best-effort and always runs — a transient subagent must
            # not outlive the node's execution even on failure.
            if sub_id is not None:
                try:
                    await self._agent_manager.teardown_subagent(sub_id)
                except Exception as exc:  # noqa: BLE001 — teardown must not mask the real error
                    logger.warning(
                        "MetaAgentNode '%s' teardown failed for %s: %s",
                        self.name, sub_id, exc,
                    )
            # completion_event reflects *real* completion (success or recorded
            # failure), not a pre-set stub signal.
            if self._completion_event is not None:
                self._completion_event.set()

        result = self._build_result(
            state,
            "completed" if error is None else "failed",
        )
        result["subagent_id"] = sub_id or ""
        result["response"] = response
        if error:
            result["error"] = error
        return result

    def _build_result(self, state: GraphState, status: str) -> dict[str, Any]:
        """Build execution result dict."""
        return {
            "agent_id": self.agent_id,
            "status": status,
            "node_name": self.name,
            "input": state.input,
            "messages_count": len(state.messages),
        }

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
