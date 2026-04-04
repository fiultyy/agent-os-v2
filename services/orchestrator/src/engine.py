"""Orchestration engine — entry point for the orchestrator service.

Provides:
- FastAPI app with agent CRUD + execution endpoints.
- Graph execution with memory integration and SSE event emission.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.graph import StateGraph, GraphState, InMemoryCheckpointStore
from src.graph.nodes import GraphNode, FunctionNode
from src.memory import MemoryService, InMemoryStore, MemoryType, MemoryScope

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

    # Create session
    await _memory_service.create_session(session_id, req.agent_id)

    # Build execution graph
    graph, _ = _build_execution_graph(req.agent_id, session_id)

    initial_state = GraphState(
        input=req.input,
        agent_id=req.agent_id,
        session_id=session_id,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        # Mark agent running
        agent["status"] = "running"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "running"})

        # Run graph step-by-step with event emission
        state = initial_state
        state.status = "running"

        # Start node
        yield _sse("node_start", {"node": "start", "status": "running"})
        state = await _run_start(state)
        yield _sse("node_complete", {
            "node": "start", "status": "done", "output": state.output or "started"
        })

        # LLM node
        yield _sse("node_start", {"node": "llm", "status": "running"})
        state = await _run_llm(state, req.agent_id, session_id)
        yield _sse("node_complete", {
            "node": "llm", "status": "done", "output": state.output
        })

        # Check if tool call needed
        if "search" in state.output.lower() or "tool_call" in state.context:
            yield _sse("node_start", {"node": "tool", "status": "running"})
            state = await _run_tool(state)
            yield _sse("node_complete", {
                "node": "tool", "status": "done", "output": state.context.get("tool_result", "")
            })

            # Feed tool result back to LLM
            yield _sse("node_start", {"node": "llm_synthesize", "status": "running"})
            state = await _run_llm_synthesize(state)
            yield _sse("node_complete", {
                "node": "llm_synthesize", "status": "done", "output": state.output
            })

        # End
        state.status = "done"
        agent["status"] = "idle"
        yield _sse("agent_status", {"agent_id": req.agent_id, "status": "idle"})
        yield _sse("execution_complete", {
            "output": state.output,
            "memory_count": len(state.memory_refs),
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _run_start(state: GraphState) -> GraphState:
    state.messages.append({"role": "system", "content": "Processing started"})
    state.context["original_input"] = state.input
    state.current_node = "start"
    state.output = "started"
    return state


async def _run_llm(state: GraphState, agent_id: str, session_id: str) -> GraphState:
    """Simulate LLM processing with memory integration."""
    # Recall relevant memories
    memories = await _memory_service.recall(
        query=state.input,
        agent_id=agent_id,
        session_id=session_id,
        top_k=3,
    )
    memory_ctx = ""
    if memories:
        memory_ctx = "\n".join(f"- {m.content}" for m in memories[:3])

    # Simulate LLM response
    user_input = state.input
    if memory_ctx:
        response = f"[LLM] Based on previous context:\n{memory_ctx}\n\nProcessing: {user_input}"
    else:
        response = f"[LLM] Processed: {user_input}"

    state.messages.append({"role": "user", "content": user_input})
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm"

    # Store to memory
    ref = await _memory_service.store(
        content=f"User: {user_input}\nAssistant: {response}",
        agent_id=agent_id,
        session_id=session_id,
        memory_type=MemoryType.SESSION,
        scope=MemoryScope.AGENT,
    )
    state.memory_refs.append(ref.id)
    return state


async def _run_tool(state: GraphState) -> GraphState:
    """Simulate tool execution."""
    tool_name = state.context.get("tool_call", "web_search")
    result = f"[Tool] {tool_name} returned: Simulated result for '{state.input}'"
    state.tool_results.append({"tool": tool_name, "result": result})
    state.context["tool_result"] = result
    state.current_node = "tool"
    return state


async def _run_llm_synthesize(state: GraphState) -> GraphState:
    """LLM synthesizes tool results into final answer."""
    tool_result = state.context.get("tool_result", "")
    response = f"[LLM] Based on tool results: {tool_result}\n\nFinal answer for: {state.input}"
    state.messages.append({"role": "assistant", "content": response})
    state.output = response
    state.current_node = "llm_synthesize"
    return state


def _build_execution_graph(agent_id: str, session_id: str) -> tuple[StateGraph, MemoryService]:
    """Build a minimal execution graph."""
    checkpoint_store = InMemoryCheckpointStore()
    graph = StateGraph("exec-graph")
    graph.set_checkpoint_store(checkpoint_store)
    return graph, _memory_service


# ── SSE helpers ──────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _sse_error(msg: str) -> AsyncGenerator[str, None]:
    yield _sse("error", {"message": msg})
    yield "data: [DONE]\n\n"


# ── CLI entry point ──────────────────────────────────────────────


def main() -> None:
    """CLI entry point: run the minimal graph demo."""
    graph, memory = _build_execution_graph("cli", "cli-session")

    async def run_demo():
        for i, user_input in enumerate([
            "Hello, Agent OS!",
            "What is the weather today?",
            "Remember my preferences",
        ], 1):
            state = GraphState(input=user_input, agent_id="cli", session_id="cli-session")
            result = await _run_llm(state, "cli", "cli-session")
            print(f"\n--- Round {i} ---")
            print(f"  Input:  {user_input}")
            print(f"  Output: {result.output}")

        memories = await memory.recall(query="", agent_id="cli", session_id="cli-session", top_k=10)
        print(f"\n=== {len(memories)} memories stored ===")

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
