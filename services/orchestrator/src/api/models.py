"""Request / Response Pydantic models for the orchestrator API."""

from __future__ import annotations

from pydantic import BaseModel


class CreateAgentRequest(BaseModel):
    name: str = "New Agent"
    description: str = ""
    model: str = "glm-4-flash"
    system_prompt: str = ""
    tools: list[str] = []


class ExecuteRequest(BaseModel):
    agent_id: str
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
