"""Orchestration engine — entry point for the orchestrator service.

Provides:
- FastAPI app for HTTP mode (production).
- CLI ``__main__`` entry point for standalone graph execution with memory.
"""

import asyncio

from fastapi import FastAPI

from src.graph import StateGraph, GraphState, InMemoryCheckpointStore
from src.graph.nodes import GraphNode, FunctionNode, LLMNode
from src.memory import MemoryService, InMemoryStore, MemoryType, MemoryScope

app = FastAPI(title="Agent OS — Orchestrator", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


class MemoryAwareNode(GraphNode):
    """Base node that automatically stores execution results in MemoryService.

    After executing the core logic, the node stores the output as a
    SESSION memory item associated with the agent and session from state.
    """

    def __init__(self, name: str, memory_service: MemoryService) -> None:
        super().__init__(name)
        self.memory = memory_service

    async def execute(self, state: GraphState) -> GraphState:
        """Execute node logic then auto-store to memory."""
        state = await self._do_work(state)
        await self._store_to_memory(state)
        return state

    async def _do_work(self, state: GraphState) -> GraphState:
        """Override in subclasses for actual work. Default: pass through."""
        return state

    async def _store_to_memory(self, state: GraphState) -> None:
        """Store the current output as a session memory."""
        if state.output and state.agent_id:
            await self.memory.store(
                content=f"[{self.name}] {state.output}",
                agent_id=state.agent_id,
                session_id=state.session_id,
                memory_type=MemoryType.SESSION,
                scope=MemoryScope.AGENT,
            )


class MemoryAwareLLMNode(MemoryAwareNode):
    """LLM node that auto-stores results to memory."""

    async def _do_work(self, state: GraphState) -> GraphState:
        """Simulate LLM processing."""
        user_input = state.input
        response = f"[LLM] Processed: {user_input}"

        state.messages.append({"role": "user", "content": user_input})
        state.messages.append({"role": "assistant", "content": response})
        state.output = response
        state.current_node = self.name
        return state


def build_minimal_graph() -> tuple[StateGraph, MemoryService]:
    """Build a minimal graph with memory integration.

    Flow: ``start`` → ``llm`` (memory-aware) → ``end``.

    Returns:
        Tuple of (configured StateGraph, MemoryService instance).
    """
    memory_service = MemoryService(InMemoryStore())
    checkpoint_store = InMemoryCheckpointStore()

    async def start(state: GraphState) -> GraphState:
        """Entry node: record user input."""
        state.messages.append({"role": "system", "content": "Processing started"})
        state.context["original_input"] = state.input
        state.current_node = "start"
        return state

    async def end(state: GraphState) -> GraphState:
        """Exit node: finalize."""
        state.current_node = "end"
        state.status = "done"
        return state

    graph = StateGraph("minimal-graph")
    graph.add_node("start", FunctionNode("start", start))
    graph.add_node("llm", MemoryAwareLLMNode("llm", memory_service))
    graph.add_node("end", FunctionNode("end", end))

    graph.add_edge("start", "llm")
    graph.add_edge("llm", "end")
    graph.set_entry_point("start")
    graph.set_checkpoint_store(checkpoint_store)

    return graph, memory_service


def main() -> None:
    """CLI entry point: run the minimal graph with memory integration."""
    graph, memory = build_minimal_graph()

    async def run_demo():
        # Simulate 3 rounds of conversation
        for i, user_input in enumerate([
            "Hello, Agent OS!",
            "What is the weather today?",
            "Remember my preferences",
        ], 1):
            state = GraphState(
                input=user_input,
                agent_id="demo-agent",
                session_id="demo-session",
            )
            result = await graph.run(state)
            print(f"\n--- Round {i} ---")
            print(f"  Input:  {user_input}")
            print(f"  Output: {result.output}")
            print(f"  Status: {result.status}")

        # Check stored memories
        memories = await memory.recall(
            query="",
            agent_id="demo-agent",
            session_id="demo-session",
            top_k=10,
        )
        print(f"\n=== MemoryService: {len(memories)} session memories stored ===")
        for m in memories:
            print(f"  [{m.memory_type.value}] {m.content[:60]}")

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
