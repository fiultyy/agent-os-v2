"""Orchestration engine — entry point for the orchestrator service.

Provides:
- FastAPI app with agent CRUD + execution endpoints.
- Graph execution with memory integration and SSE event emission.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.graph import StateGraph, GraphState, InMemoryCheckpointStore
from src.graph.nodes import FunctionNode
from src.memory import MemoryService, InMemoryStore, MemoryType, MemoryScope


# ── LLM Client ────────────────────────────────────────────────────


class LLMClient:
    """Lightweight LLM client using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.default_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> str:
        """Send messages and return the assistant content string."""
        if not self.api_key:
            return "[LLM fallback] No API key configured"

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                return f"[LLM error] {exc}"


_llm_client = LLMClient()

app = FastAPI(title="Agent OS — Orchestrator", version="0.1.0", redirect_slashes=False)

# ── In-memory agent registry ────────────────────────────────────

_agents: dict[str, dict[str, Any]] = {}
_memory_service = MemoryService(InMemoryStore())


class CreateAgentRequest(BaseModel):
    name: str = "New Agent"
    description: str = ""
    model: str = "gpt-4o-mini"
    tools: list[str] = []


class ExecuteRequest(BaseModel):
    agent_id: str
    input: str
    session_id: str = ""


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Agent CRUD ───────────────────────────────────────────────────


@app.post("/agents")
async def create_agent(req: CreateAgentRequest) -> dict:
    agent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    agent = {
        "id": agent_id,
        "name": req.name,
        "description": req.description,
        "status": "idle",
        "model": req.model,
        "tools": req.tools,
        "created_at": now,
        "updated_at": now,
    }
    _agents[agent_id] = agent
    await _memory_service.init_agent_blocks(agent_id)
    return agent


@app.get("/agents")
async def list_agents() -> list[dict]:
    return list(_agents.values())


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    agent = _agents.get(agent_id)
    if not agent:
        return {"error": "Agent not found"}
    return agent


@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict:
    if agent_id in _agents:
        del _agents[agent_id]
        return {"deleted": True}
    return {"error": "Agent not found"}


# ── Node handler functions (used by FunctionNode) ────────────────


async def _node_start(state: GraphState) -> GraphState:
    """Initialize the execution pipeline."""
    state.messages.append({"role": "system", "content": "Processing started"})
    state.context["original_input"] = state.input
    state.current_node = "start"
    state.output = "started"
    return state


async def _node_llm(state: GraphState) -> GraphState:
    """LLM processing with real API call and memory integration."""
    agent_id = state.agent_id
    session_id = state.session_id

    memories = await _memory_service.recall(
        query=state.input,
        agent_id=agent_id,
        session_id=session_id,
        top_k=3,
    )

    # Build message list for the LLM
    llm_messages: list[dict[str, Any]] = []
    if memories:
        memory_ctx = "\n".join(f"- {m.content}" for m in memories[:3])
        llm_messages.append({"role": "system", "content": f"Relevant context from memory:\n{memory_ctx}"})

    user_input = state.input
    # Include prior conversation history
    llm_messages.extend(state.messages)
    llm_messages.append({"role": "user", "content": user_input})

    response = await _llm_client.chat(llm_messages)

    state.messages.append({"role": "user", "content": user_input})
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm"

    ref = await _memory_service.store(
        content=f"User: {user_input}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
    )
    state.memory_refs.append(ref.id)

    # Flag whether a tool call is needed (checked by conditional edge)
    if "search" in response.lower() or "tool_call" in state.context:
        state.context["needs_tool"] = True
    else:
        state.context["needs_tool"] = False

    return state


async def _node_tool(state: GraphState) -> GraphState:
    """Simulate tool execution."""
    tool_name = state.context.get("tool_call", "web_search")
    result = f"[Tool] {tool_name} returned: Simulated result for '{state.input}'"
    state.tool_results.append({"tool": tool_name, "result": result})
    state.context["tool_result"] = result
    state.current_node = "tool"
    return state


async def _node_llm_synthesize(state: GraphState) -> GraphState:
    """LLM synthesizes tool results into final answer."""
    tool_result = state.context.get("tool_result", "")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Synthesize the tool results into a final answer for the user."},
        {"role": "user", "content": f"Original question: {state.input}\n\nTool results: {tool_result}"},
    ]
    response = await _llm_client.chat(messages)
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm_synthesize"
    return state


# ── Graph builder ────────────────────────────────────────────────


def _build_execution_graph() -> StateGraph:
    """Build the orchestration graph with nodes and edges.

    Graph topology::

        start → llm → [needs_tool?] → tool → llm_synthesize → end
                         ↓ no
                        end
    """
    graph = StateGraph("exec-graph")
    graph.set_checkpoint_store(InMemoryCheckpointStore())

    # Register nodes
    graph.add_node("start", FunctionNode("start", _node_start))
    graph.add_node("llm", FunctionNode("llm", _node_llm))
    graph.add_node("tool", FunctionNode("tool", _node_tool))
    graph.add_node("llm_synthesize", FunctionNode("llm_synthesize", _node_llm_synthesize))

    # Unconditional edges
    graph.add_edge("start", "llm")
    graph.add_edge("tool", "llm_synthesize")

    # Conditional edge: llm → tool (if needed) or end
    graph.add_conditional_edge(
        source="llm",
        targets={
            "tool": "tool",
            "__default__": "",
        },
        condition=lambda state: "tool" if state.context.get("needs_tool") else "__default__",
    )

    graph.set_entry_point("start")
    return graph


# ── Execution (SSE) ─────────────────────────────────────────────


@app.post("/execute")
async def execute(req: ExecuteRequest) -> StreamingResponse:
    """Execute an agent graph and stream node status via SSE."""
    agent = _agents.get(req.agent_id)
    if not agent:
        return StreamingResponse(
            _sse_error("Agent not found"),
            media_type="text/event-stream",
        )

    session_id = req.session_id or str(uuid.uuid4())
    await _memory_service.create_session(session_id, req.agent_id)

    graph = _build_execution_graph()
    initial_state = GraphState(
        input=req.input,
        agent_id=req.agent_id,
        session_id=session_id,
    )

    # Queue for SSE events produced by the graph callback
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_node_complete(node_name: str, state: GraphState) -> None:
        """Callback fired after each graph node completes — pushes SSE events."""
        if node_name == "start":
            await event_queue.put(_sse("node_start", {"node": "start", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "start", "status": "done", "output": state.output or "started",
            }))
        elif node_name == "llm":
            await event_queue.put(_sse("node_start", {"node": "llm", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "llm", "status": "done", "output": state.output,
            }))
        elif node_name == "tool":
            await event_queue.put(_sse("node_start", {"node": "tool", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "tool", "status": "done",
                "output": state.context.get("tool_result", ""),
            }))
        elif node_name == "llm_synthesize":
            await event_queue.put(_sse("node_start", {"node": "llm_synthesize", "status": "running"}))
            await event_queue.put(_sse("node_complete", {
                "node": "llm_synthesize", "status": "done", "output": state.output,
            }))

    async def event_stream() -> AsyncGenerator[str, None]:
        agent["status"] = "running"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "running"})

        # Run graph in background, feeding SSE via the queue
        async def run_graph():
            try:
                final_state = await graph.run(initial_state, on_node_complete=on_node_complete)
                agent["status"] = "idle"
                await event_queue.put(_sse("agent_status", {"agent_id": req.agent_id, "status": "idle"}))
                await event_queue.put(_sse("execution_complete", {
                    "output": final_state.output,
                    "memory_count": len(final_state.memory_refs),
                }))
            except Exception as exc:
                agent["status"] = "idle"
                await event_queue.put(_sse("error", {"message": str(exc)}))
            finally:
                await event_queue.put(None)  # sentinel

        task = asyncio.create_task(run_graph())

        # Drain queue → SSE stream
        while True:
            item = await event_queue.get()
            if item is None:
                break
            yield item

        yield "data: [DONE]\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── SSE helpers ──────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _sse_error(msg: str) -> AsyncGenerator[str, None]:
    yield _sse("error", {"message": msg})
    yield "data: [DONE]\n\n"


# ── CLI entry point ──────────────────────────────────────────────


def main() -> None:
    """CLI entry point: run the minimal graph demo."""

    async def run_demo():
        graph = _build_execution_graph()

        for i, user_input in enumerate([
            "Hello, Agent OS!",
            "What is the weather today?",
            "Remember my preferences",
        ], 1):
            state = GraphState(input=user_input, agent_id="cli", session_id="cli-session")
            result = await graph.run(state)
            print(f"\n--- Round {i} ---")
            print(f"  Input:  {user_input}")
            print(f"  Output: {result.output}")

        memories = await _memory_service.recall(
            query="", agent_id="cli", session_id="cli-session", top_k=10,
        )
        print(f"\n=== {len(memories)} memories stored ===")

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
