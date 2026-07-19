"""Request / Response Pydantic models for the orchestrator API."""

from __future__ import annotations

from pydantic import BaseModel


class ExecuteRequest(BaseModel):
    agent_id: str
    input: str
    session_id: str = ""


class ExecuteParallelRequest(BaseModel):
    """P2: 多视角并行分析请求(multi-perspective fan-out → fan-in 综合)。

    与 :class:`ExecuteRequest` 完全独立(R3 — 不给 ExecuteRequest 加 mode 开关):
    多 agent 并行用独立的 model + 独立的 ``/v1/execute_parallel`` 端点,避免触碰
    单 agent 线性图。``agent_ids`` 是一组 *真实* agent_id(来自 ``_state.agents``),
    每个跑一条 ``run_agent_turn`` 分支。
    """

    agent_ids: list[str]
    input: str
    session_id: str = ""


class OrchestrateRequest(BaseModel):
    """P3: 多 agent 编排请求(fan-out N 角色 subagent → fan-in → orchestrator 综合)。

    与 :class:`ExecuteRequest` / :class:`ExecuteParallelRequest` 完全独立(R3 —
    不给既有 request 加 mode 开关):用独立的 model + 独立的
    ``/v1/orchestrate`` 端点,避免触碰单 agent 线性图 / P2 并行图。

    ``orchestrator_agent_id`` 是 ``_state.agents`` 里的真实持久 agent(综合 +
    记忆单点落库出口)。``sub_agents`` 是 subagent 角色规格列表,每项形如::

        {"role": "researcher", "config": {...}, "input": "...", "system_prompt": "..."}

    每个 role 由 :class:`AgentWorkerNode` 真实 spawn 一个临时 subagent
    (create_subagent → run_agent_turn → teardown),区别于 P2 的预存 agent 并行。
    """

    orchestrator_agent_id: str
    sub_agents: list[dict]
    input: str
    session_id: str = ""


class StoreMemoryRequest(BaseModel):
    content: str
    agent_id: str = ""
    session_id: str = ""
    memory_type: str = "session"
    scope: str = "agent"
    importance: float = 0.5
    # When True the route awaits the ① IngestorAgent LLM extraction and
    # returns entities/identity_category; when False (default) ingestion is
    # fire-and-forget.
    sync_extract: bool = False


class ChatRequest(BaseModel):
    message: str
    agent_id: str = ""
    session_id: str = ""


class SendMessageRequest(BaseModel):
    sender_id: str
    recipient_id: str | None = None
    session_id: str = ""
    workspace_id: str = ""
    content: str
    message_type: str = "task"
    priority: int = 1


class GrantPermissionRequest(BaseModel):
    grantor_id: str
    grantee_id: str
    target_agent_id: str
    level: int = 2
    expires_at: str | None = None


class MemoryNotifyRequest(BaseModel):
    agent_id: str = ""
    force: bool = False
    # When True fire a ④ CuratorAgent pass as an independent fire-and-forget
    # task (NEVER inserted into the synchronous run_maintenance Zero-LLM
    # chain). Distinct from the default deterministic prune/forget/migrate.
    curate: bool = False


class MemoryConsolidateRequest(BaseModel):
    agent_id: str = ""
    session_id: str = ""
    messages: list[dict] | None = None
    timeout: float = 8.0
    # mode=merge → trigger ② ConsolidatorAgent (episodic→semantic merge).
    # default (task_consolidator) → task-post experience sedimentation.
    mode: str = "task_consolidator"
