"""Graph state machine — the core orchestration model.

Exports:
    - :class:`StateGraph` — the main graph orchestrator.
    - :class:`GraphState` — runtime state dataclass.
    - :class:`GraphNode` — abstract node base class.
    - :class:`Edge` / :class:`ConditionalEdge` — edge types.
    - :class:`InMemoryCheckpointStore` — state checkpoint persistence.
"""

from typing import Callable

from src.graph.state import GraphState
from src.graph.nodes import GraphNode
from src.graph.edges import Edge, ConditionalEdge

__all__ = [
    "GraphState",
    "GraphNode",
    "Edge",
    "ConditionalEdge",
    "StateGraph",
    "InMemoryCheckpointStore",
]


class InMemoryCheckpointStore:
    """In-memory store for graph state snapshots.

    Each call to :meth:`save` persists a deep copy of the current
    state keyed by ``(graph_id, checkpoint_id)``. Use :meth:`load`
    to restore a previously saved checkpoint.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, GraphState] = {}

    def save(self, graph_id: str, checkpoint_id: str, state: GraphState) -> None:
        """Save a snapshot of *state*."""
        key = f"{graph_id}:{checkpoint_id}"
        self._checkpoints[key] = GraphState.from_dict(state.to_dict())

    def load(self, graph_id: str, checkpoint_id: str) -> GraphState | None:
        """Load a previously saved checkpoint, or ``None`` if not found."""
        key = f"{graph_id}:{checkpoint_id}"
        cp = self._checkpoints.get(key)
        if cp is None:
            return None
        return GraphState.from_dict(cp.to_dict())

    def latest(self, graph_id: str) -> GraphState | None:
        """Return the most recently saved checkpoint for *graph_id*."""
        cps = self.list_checkpoints(graph_id)
        if not cps:
            return None
        return self.load(graph_id, cps[-1])

    def list_checkpoints(self, graph_id: str) -> list[str]:
        """Return all checkpoint IDs for the given *graph_id* in order."""
        prefix = f"{graph_id}:"
        return [k[len(prefix):] for k in sorted(self._checkpoints) if k.startswith(prefix)]


class StateGraph:
    """Directed graph orchestrator that executes nodes in sequence.

    Usage::

        graph = StateGraph("my-graph")
        graph.add_node("start", start_node)
        graph.add_node("process", process_node)
        graph.add_node("end", end_node)
        graph.add_edge("start", "process")
        graph.add_edge("process", "end")
        graph.set_entry_point("start")

        result = await graph.run(initial_state)

    Supports:
    - Unconditional edges (fixed source -> target).
    - Conditional edges (source -> target based on state field).
    - Automatic checkpointing after each node execution.
    - Resume from last checkpoint.
    """

    def __init__(self, graph_id: str) -> None:
        self.graph_id = graph_id
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, list[Edge | ConditionalEdge]] = {}
        self._entry_point: str = ""
        self._checkpoint_store: InMemoryCheckpointStore | None = None
        self._checkpoint_counter: int = 0

    def add_node(self, name: str, node: GraphNode) -> "StateGraph":
        """Register a node with the given *name*."""
        self._nodes[name] = node
        return self

    def add_edge(self, source: str, target: str) -> "StateGraph":
        """Add an unconditional edge from *source* to *target*."""
        edge = Edge(source=source, target=target)
        self._edges.setdefault(source, []).append(edge)
        return self

    def add_conditional_edge(
        self,
        source: str,
        targets: dict[str, str],
        condition_field: str = "status",
        condition: Callable | None = None,
    ) -> "StateGraph":
        """Add a conditional edge that routes based on a state field.

        Args:
            source: Source node name.
            targets: Mapping from field value to target node name.
                Use ``"__default__"`` as fallback.
            condition_field: State attribute to check (default ``"status"``).
            condition: Optional sync callable overriding field lookup.
        """
        edge = ConditionalEdge(
            source=source,
            targets=targets,
            condition_field=condition_field,
            condition=condition,
        )
        self._edges.setdefault(source, []).append(edge)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        """Set the first node to execute when the graph runs."""
        if name not in self._nodes:
            raise ValueError(f"Node {name!r} not found in graph")
        self._entry_point = name
        return self

    def set_checkpoint_store(self, store: InMemoryCheckpointStore) -> "StateGraph":
        """Attach a checkpoint store for state persistence."""
        self._checkpoint_store = store
        return self

    async def run(self, initial_state: GraphState) -> GraphState:
        """Execute the graph starting from the entry point.

        Walks the graph node by node, resolving conditional edges
        as needed, until no more edges are found (graph ends).

        Returns:
            The final graph state after all nodes have executed.
        """
        if not self._entry_point:
            raise RuntimeError("Entry point not set — call set_entry_point() first")

        state = initial_state
        state.status = "running"
        current_node_name: str | None = self._entry_point

        while current_node_name:
            node = self._nodes.get(current_node_name)
            if node is None:
                state.errors.append(f"Node {current_node_name!r} not found")
                break

            state.current_node = current_node_name
            state = await node.execute(state)

            # Save checkpoint after each node
            if self._checkpoint_store is not None:
                self._checkpoint_counter += 1
                cp_id = f"step-{self._checkpoint_counter}"
                self._checkpoint_store.save(self.graph_id, cp_id, state)

            # Resolve next node
            current_node_name = self._resolve_next(current_node_name, state)

        state.status = "done"
        return state

    async def resume(self) -> GraphState:
        """Resume execution from the latest checkpoint.

        Raises:
            RuntimeError: If no checkpoint store or no checkpoints found.
        """
        if self._checkpoint_store is None:
            raise RuntimeError("No checkpoint store configured")

        state = self._checkpoint_store.latest(self.graph_id)
        if state is None:
            raise RuntimeError(f"No checkpoints found for graph {self.graph_id!r}")

        # Resume from the node after the last executed one
        current_node_name = self._resolve_next(state.current_node, state)

        while current_node_name:
            node = self._nodes.get(current_node_name)
            if node is None:
                state.errors.append(f"Node {current_node_name!r} not found")
                break

            state.current_node = current_node_name
            state = await node.execute(state)

            if self._checkpoint_store is not None:
                self._checkpoint_counter += 1
                cp_id = f"step-{self._checkpoint_counter}"
                self._checkpoint_store.save(self.graph_id, cp_id, state)

            current_node_name = self._resolve_next(current_node_name, state)

        state.status = "done"
        return state

    def _resolve_next(self, current: str, state: GraphState) -> str:
        """Determine the next node after *current* based on edges and state."""
        edges = self._edges.get(current, [])
        for edge in edges:
            return edge.route(state)
        return ""
