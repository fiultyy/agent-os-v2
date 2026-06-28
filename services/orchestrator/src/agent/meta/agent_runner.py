"""run_agent_turn — 公共子 agent 单轮执行函数。

P1 多 agent 通电的最小执行原语(见 docs/multi-agent-poweron-roadmap.md §P1)。
设计目标:把一个 *临时* subagent 跑一轮 LLM 对话并返回响应字符串,**完全不碰**
顶层 agent 的 GraphState / context / 记忆模块 —— 子 agent 是临时态,记忆只在
fan-in 后由主 agent 单点落库。

红线
----
R1 (记忆零触碰):
    本函数体 *不调用* ``memory_event_bus.emit`` / ``_trigger_ingest`` /
    ``_trigger_kg_extraction`` / ``memory_service.*`` 中的任何一个。grep 本文件
    零 memory 调用即守恒此红线。子 agent 不沉淀记忆。

R2 (主路径冻结):
    本函数 *不 import 也不修改* ``_build_execution_graph`` / ``_node_llm`` /
    ``chat.py`` 的 ``/execute`` 线性图。它直接构造本地 messages 并调用
    ``_state.llm_client.chat``,与顶层 agent 的执行图物理隔离。

上下文隔离:
    独立 ``agent_id`` + 独立 ``messages`` 列表(本地构建)。不复用顶层 agent 的
    GraphState/messages/context,避免串味。
"""

from __future__ import annotations

import logging
from typing import Any

from src.services import _state

logger = logging.getLogger(__name__)


async def run_agent_turn(
    agent_id: str,
    input: str,
    session_id: str,
    system_prompt: str | None = None,
) -> str:
    """Run one LLM turn for a transient subagent and return the response.

    Reads the subagent's config (``system_prompt`` / ``model``) from the in-memory
    ``_state.agents`` registry, builds a *local* message list, and calls the LLM
    client directly. This is the multi-agent orchestration equivalent of a single
    chat round, deliberately stripped of every memory-trigger side effect (R1) and
    decoupled from the main ``/execute`` linear graph (R2).

    Args:
        agent_id: Subagent id (as returned by ``agent_manager.create_subagent``).
        input: The user/task input for this turn.
        session_id: Session id for logging/correlation only — *no* memory hook
            attaches to it here (memory writes happen fan-in, on the main agent).
        system_prompt: Optional system prompt override. When ``None`` the prompt
            is taken from the subagent's stored config, falling back to a neutral
            default.

    Returns:
        The assistant's reply as a string. On a *non-fatal* degradation (agent
        config missing, or no LLM client wired) an error string is returned rather
        than raised — the orchestration caller surfaces it via ``subgraph_results``
        without aborting sibling agents.

    None-safe contract:
        - ``_state.agents`` may not contain ``agent_id`` (race / teardown) → the
          prompt/model default; we still attempt the LLM call with defaults.
        - ``_state.llm_client`` may be ``None`` (LLM not wired, e.g. cold start /
          degraded mode) → return ``"[run_agent_turn error] no llm_client"`` and
          skip the call entirely (no exception bubbles up).
    """
    # Pull subagent config from the in-memory registry (None-safe). A subagent is
    # created by create_subagent and lives in _state.agents until teardown.
    agent_cfg: dict[str, Any] | None = _state.agents.get(agent_id)
    if agent_cfg is not None:
        cfg_system = agent_cfg.get("system_prompt") or ""
        cfg_model = agent_cfg.get("model")
    else:
        cfg_system = ""
        cfg_model = None

    # Effective system prompt: explicit arg > stored config > neutral default.
    effective_system = system_prompt if system_prompt is not None else cfg_system
    if not effective_system:
        effective_system = "You are a helpful sub-agent. Complete the assigned task."

    # ── Build a LOCAL message list (context isolation) ───────────────────────
    # The system message is lifted to Anthropic top-level ``system`` by
    # llm_client._to_anthropic (and ignored on the OpenAI channel's system slot
    # via the same conversion). We never reuse the parent agent's GraphState.
    messages: list[dict[str, Any]] = [{"role": "system", "content": effective_system}]
    if input:
        messages.append({"role": "user", "content": input})

    # ── None-safe LLM call ───────────────────────────────────────────────────
    llm = _state.llm_client
    if llm is None:
        # LLM not wired — degrade to an explicit error string (no raise). Keeps
        # sibling agents running; the orchestrator surfaces this in results.
        logger.warning("run_agent_turn: llm_client is None (agent=%s)", agent_id)
        return f"[run_agent_turn error] no llm_client for agent {agent_id}"

    try:
        # Direct LLM call: no tools, no native function-calling, no memory hooks.
        # kwargs (max_tokens/temperature) intentionally omitted to use LLM client
        # defaults — subagent turns are short prompts, not long generations.
        response: str = await llm.chat(messages, model=cfg_model)
    except Exception as exc:  # noqa: BLE001 — LLM failures degrade, not abort
        logger.warning("run_agent_turn: LLM call failed (agent=%s): %s", agent_id, exc)
        return f"[run_agent_turn error] llm call failed: {exc}"

    return response or ""
