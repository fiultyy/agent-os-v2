"""AgentWorkerNode — P3 多 agent 编排的 worker 节点。

每个 :class:`AgentWorkerNode` 代表一个 *角色*(subagent role),在
``execute`` 里跑完整的 **spawn → run → teardown** 闭环:

    create_subagent(agent_type=<role>, config, parent_id, session_id)
        → run_agent_turn(sub_id, input, session_id, system_prompt)
        → teardown_subagent(sub_id)          # finally 必跑

这是 P3 集成终点的核心原语,与 P2 的 :func:`_build_parallel_graph` 有本质区别:

- **P2** 分支复用 *已存在* 的真实 agent_id(预存 agent),只调
  ``run_agent_turn`` —— 不 spawn / 不 teardown。
- **P3** 每个 worker 是一个 *临时* subagent:运行时由
  ``create_subagent`` 真实生成,跑完一轮即 ``teardown_subagent`` 销毁,
  生命周期完全自洽(区别于 P2 预存 agent 并行)。

设计继承 :class:`MetaAgentNode` 而非直接继承 ``GraphNode``,以复用其
``_has_real_lifecycle`` 探测 / ``_build_result`` / completion_event 语义。
但 worker 的闭环逻辑更直接(无 spawner / 无 spawn_config 间接层):它直接持
有 ``agent_manager`` + role + config,在 ``execute`` 里完成整个 spawn→run→
teardown,结果写入 ``state.parallel_results[self.name]``(供
:class:`FanInNode` 读取),同时返回新 state。

红线
----
R1(记忆零触碰):
    worker 复用 P1 产出的 :func:`run_agent_turn`(已剥离全部 memory 触发)。
    本文件体 *不调用* ``memory_event_bus.emit`` / ``_trigger_*`` / ``memory_*``
    中的任何一个。grep 本文件零 memory 调用即守恒此红线。子 agent 不沉淀记忆 ——
    记忆只在 fan-in 后由主 agent(orchestrator)单点落库。

R2(主路径冻结):
    本文件 *不 import 也不修改* ``_build_execution_graph`` / ``/execute`` /
    ``_build_parallel_graph`` —— 物理隔离。

R5(teardown is_subagent 守卫):
    ``teardown_subagent`` 本身带 ``is_subagent is True`` 守卫(P0 契约);worker
    不需要重复守护,但 ``finally`` 必跑 teardown 保证临时 subagent 不泄漏。

R6(create_subagent 契约):
    ``create_subagent`` 返回 ``{"id": <uuid>}``(P0 锁定);worker 取 ``["id"]``。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agent.meta.meta_agent_node import MetaAgentNode
from src.graph.state import GraphState

logger = logging.getLogger(__name__)


class AgentWorkerNode(MetaAgentNode):
    """每个 worker 代表一个 subagent 角色(spawn → run → teardown 闭环)。

    与父类 :class:`MetaAgentNode` 的差异:

    - 不依赖 ``spawner`` / ``spawn_config`` 间接层:worker 直接持有 ``role``
      + ``config``,在 ``execute`` 里 *直接* 调 ``agent_manager.create_subagent``
      (接通生产,P0 已就绪)。
    - 结果写入 ``state.parallel_results[self.name]``(P3 多 agent 图的
      :class:`ParallelNode` 分支汇聚契约),供后续 :class:`FanInNode` 读取。
    - 完全独立于顶层 orchestrator 的 GraphState / context / 记忆(R1 / 上下文
      隔离):分支跑在 ParallelNode 克隆出的 state 上,互不串味。

    Attributes:
        role: subagent 角色名(作为 ``agent_type`` 传给 create_subagent)。
        config: subagent 配置(name/model/system_prompt/tools 等)。
        turn_input: 本 worker 这一轮的输入(默认回退到 state.input)。
        system_prompt: 显式 system prompt 覆盖(优先于 config)。
    """

    def __init__(
        self,
        name: str,
        role: str,
        agent_manager: Any,
        config: dict[str, Any] | None = None,
        *,
        orchestrator_id: str = "",
        session_id: str = "",
        turn_input: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        """Initialize an AgentWorkerNode.

        Args:
            name: 节点名(图内唯一,亦作 parallel_results 的 key)。
            role: subagent 角色名,传给 ``create_subagent(agent_type=...)``。
            agent_manager: 提供 ``create_subagent`` / ``teardown_subagent``
                的管理器(P0 契约)。``_has_real_lifecycle`` 守卫其存在性。
            config: subagent 配置 dict(name/model/system_prompt/tools 等)。
            orchestrator_id: 父 orchestrator agent_id,作为 ``parent_id`` 传入
                create_subagent(便于溯源)。
            session_id: 会话 id,传给 create_subagent / run_agent_turn。
            turn_input: 显式输入覆盖;``None`` 时取 ``state.input``。
            system_prompt: 显式 system prompt 覆盖;``None`` 时用
                ``config["system_prompt"]``,再回退中性默认。
        """
        # 不传 spawner / spawn_config / agent_id —— worker 自管生命周期。
        super().__init__(
            name=name,
            agent_id=None,        # 由 create_subagent 在 execute 内生成
            spawn_config=None,
            spawner=None,
            agent_manager=agent_manager,
            session_id=session_id,
        )
        self.role = role
        self.config = dict(config or {})
        self.orchestrator_id = orchestrator_id
        self._explicit_input = turn_input
        self._explicit_system_prompt = system_prompt

    # ── 主闭环:spawn → run → teardown ───────────────────────────────

    async def execute(self, state: GraphState) -> GraphState:
        """Run the full spawn → run → teardown closed loop for this worker.

        Overrides :meth:`MetaAgentNode.execute` to drive a *direct* closed loop
        (no spawner indirection). The flow:

        1. ``create_subagent(agent_type=role, config, parent_id, session_id)``
           → ``{"id": sub_id}`` (P0 / R6). Records ``sub_id`` for teardown.
        2. ``run_agent_turn(sub_id, input, session_id, system_prompt)`` → the
           subagent's LLM response string (P1 primitive, R1 — zero memory).
        3. ``teardown_subagent(sub_id)`` → **always** invoked in ``finally``
           (R5: best-effort, is_subagent guard lives in teardown itself).

        The result is written into ``state.parallel_results[self.name]`` (a
        ``{"branch", "output", "subagent_id", "status"}`` dict), matching the
        shape :class:`ParallelNode` / :class:`FanInNode` expect. ``self.status``
        is ``completed`` on success, ``failed`` on any error (degrade, not
        abort — sibling workers in the ParallelNode keep running).
        """
        self._cancel_event = asyncio.Event()
        self._completion_event = asyncio.Event()
        self.status = "running"
        state.current_node = self.name

        sub_id: str | None = None
        response: str = ""
        error: str | None = None

        # ── STUB 兼容:无真实 lifecycle → 空壳(不破坏旧 mock)──────────
        if not self._has_real_lifecycle():
            self.status = "failed"
            error = "no real lifecycle (agent_manager missing create_subagent/teardown_subagent)"
            self._write_branch_result(state, sub_id=None, response="", error=error)
            if self._completion_event is not None:
                self._completion_event.set()
            return state

        turn_input = (
            self._explicit_input
            if self._explicit_input is not None
            else (state.input or self.config.get("input", "") or "")
        )
        system_prompt = (
            self._explicit_system_prompt
            if self._explicit_system_prompt is not None
            else self.config.get("system_prompt")
        )
        session_id = self._session_id or state.session_id

        # 局部 import:run_agent_turn 是 P1 原语(R1 剥离记忆)。
        from src.agent.meta.agent_runner import run_agent_turn

        try:
            # 1) spawn — create_subagent(P0 契约 {"id": ...})
            spawn_result = await self._agent_manager.create_subagent(
                agent_type=self.role,
                config=self.config,
                parent_id=self.orchestrator_id,
                session_id=session_id,
            )
            raw_id = (
                spawn_result.get("id")
                if isinstance(spawn_result, dict)
                else spawn_result
            )
            # 空字符串视为无 id(跳过 run 且不 teardown 空 id)。
            sub_id = (
                raw_id
                if (isinstance(raw_id, str) and raw_id.strip())
                else None
            )

            if not sub_id:
                error = "create_subagent returned no id"
            else:
                # 2) run — run_agent_turn(R1 剥离记忆 / 上下文隔离)
                response = await run_agent_turn(
                    agent_id=sub_id,
                    input=turn_input,
                    session_id=session_id,
                    system_prompt=system_prompt,
                )
        except asyncio.CancelledError:
            error = "Cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — degrade, record, do not abort
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("AgentWorkerNode '%s' closed loop failed", self.name)
        finally:
            # 3) teardown — finally 必跑(R5 守卫在 teardown_subagent 内)。
            if sub_id is not None:
                try:
                    await self._agent_manager.teardown_subagent(sub_id)
                except Exception as exc:  # noqa: BLE001 — teardown 不掩盖真错误
                    logger.warning(
                        "AgentWorkerNode '%s' teardown failed for %s: %s",
                        self.name, sub_id, exc,
                    )
            if self._completion_event is not None:
                self._completion_event.set()

        self.status = "completed" if error is None else "failed"
        self._write_branch_result(
            state, sub_id=sub_id, response=response, error=error,
        )
        return state

    # ── helpers ──────────────────────────────────────────────────────

    def _write_branch_result(
        self,
        state: GraphState,
        *,
        sub_id: str | None,
        response: str,
        error: str | None = None,
    ) -> None:
        """Write this worker's outcome into ``state.parallel_results[self.name]``.

        Shape matches what :class:`FanInNode` reads: ``{"branch", "output",
        "status", ...}``. The branch list is appended to so multiple workers
        (each its own node) accumulate under distinct keys; the orchestrator's
        :class:`ParallelNode` collects them under its own name.

        ``state.output`` is *also* set to the response so that
        :class:`ParallelNode`'s ``branch_data["output"]``(built from
        ``branch_state.output``) carries the worker's LLM output — that is what
        :class:`FanInNode` ultimately aggregates. Without this the parallel
        branch's output would be dropped at the ParallelNode boundary.
        """
        entry: dict[str, Any] = {
            "branch": self.name,
            "role": self.role,
            "subagent_id": sub_id or "",
            "output": response or "",
            "status": self.status,
        }
        if error:
            entry["error"] = error
        state.parallel_results.setdefault(self.name, []).append(entry)
        # 关键:ParallelNode 从 branch_state.output 取 branch_data["output"];
        # 必须把响应写到 state.output,FanInNode 才能汇聚到。
        state.output = response or ""
        # 也回写到 subgraph_results(供通用节点状态查询 / MetaAgentNode 兼容)。
        state.subgraph_results[self.name] = entry
        state.metadata[f"{self.name}_status"] = self.status

    def __repr__(self) -> str:
        return (
            f"AgentWorkerNode(name={self.name!r}, role={self.role!r}, "
            f"status={self.status!r})"
        )
