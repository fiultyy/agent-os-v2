"""Graph state machine — the core orchestration model.

Exports:
    - :class:`StateGraph` — the main graph orchestrator.
    - :class:`GraphState` — runtime state dataclass.
    - :class:`GraphNode` — abstract node base class.
    - :class:`SubgraphNode` — node wrapping a nested StateGraph.
    - :class:`ParallelNode` — node executing multiple branches concurrently.
    - :class:`FanInNode` — node that waits for multiple branches to complete.
    - :class:`Edge` / :class:`ConditionalEdge` — edge types.
    - :class:`InMemoryCheckpointStore` — state checkpoint persistence.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Awaitable, Callable

from src.graph.state import GraphState
from src.graph.nodes import GraphNode
from src.graph.edges import Edge, ConditionalEdge

logger = logging.getLogger(__name__)

__all__ = [
    "GraphState",
    "GraphNode",
    "SubgraphNode",
    "ParallelNode",
    "FanInNode",
    "Edge",
    "ConditionalEdge",
    "StateGraph",
    "InMemoryCheckpointStore",
]


# ── Checkpoint store ──────────────────────────────────────────────────


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


# ── Special node types ────────────────────────────────────────────────


class SubgraphNode(GraphNode):
    """A node that wraps a nested StateGraph.

    Executes the sub-graph with a cloned copy of the incoming state,
    then merges the sub-graph's output back into the parent state.

    Attributes:
        subgraph: The nested StateGraph to execute.
        merge_strategy: How to merge results back.
            ``"replace"`` — sub-graph output replaces parent output.
            ``"append"`` — sub-graph output appended to messages.
            ``"context"`` — stored in ``state.subgraph_results[name]``.
    """

    def __init__(
        self,
        name: str,
        subgraph: "StateGraph",
        merge_strategy: str = "context",
    ) -> None:
        super().__init__(name)
        self.subgraph = subgraph
        self.merge_strategy = merge_strategy

    async def execute(self, state: GraphState) -> GraphState:
        """Execute the sub-graph and merge results back."""
        child_state = state.clone()
        child_state.status = "idle"

        result = await self.subgraph.run(child_state)

        if self.merge_strategy == "replace":
            state.output = result.output
            state.messages = result.messages
            state.context.update(result.context)
        elif self.merge_strategy == "append":
            if result.messages:
                state.messages.extend(result.messages)
            if result.output:
                state.messages.append({"role": "system", "content": result.output})
        elif self.merge_strategy == "context":
            state.subgraph_results[self.name] = result.to_dict()

        state.current_node = self.name
        return state


class ParallelNode(GraphNode):
    """A node that executes multiple branches concurrently.

    Each branch is a list of GraphNodes executed sequentially.
    All branches run in parallel via ``asyncio.gather``, and
    results are collected into ``state.parallel_results[name]``.

    Attributes:
        branches: Mapping of branch name to list of nodes.
        max_concurrency: Optional limit on parallel branches.
    """

    def __init__(
        self,
        name: str,
        branches: dict[str, list[GraphNode]],
        max_concurrency: int | None = None,
    ) -> None:
        super().__init__(name)
        self.branches = branches
        self.max_concurrency = max_concurrency

    async def execute(self, state: GraphState) -> GraphState:
        """Execute all branches in parallel and collect results."""
        semaphore = (
            asyncio.Semaphore(self.max_concurrency)
            if self.max_concurrency
            else None
        )

        async def _run_branch(
            branch_name: str, nodes: list[GraphNode],
        ) -> tuple[str, dict[str, Any]]:
            branch_state = state.clone()
            branch_state.status = "running"

            if semaphore:
                await semaphore.acquire()
            try:
                for node in nodes:
                    branch_state = await node.execute(branch_state)
            finally:
                if semaphore:
                    semaphore.release()

            return branch_name, {
                "output": branch_state.output,
                "status": branch_state.status,
                "errors": branch_state.errors,
                "tool_results": branch_state.tool_results,
            }

        tasks = [
            asyncio.create_task(_run_branch(name, nodes))
            for name, nodes in self.branches.items()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        branch_outputs: list[dict[str, Any]] = []
        for item in results:
            if isinstance(item, Exception):
                branch_outputs.append({"output": "", "status": "error", "error": str(item)})
            else:
                branch_name, branch_data = item
                branch_data["branch"] = branch_name
                branch_outputs.append(branch_data)

        state.parallel_results[self.name] = branch_outputs
        state.current_node = self.name
        return state


class FanInNode(GraphNode):
    """A node that waits for parallel branches to complete and merges results.

    Reads ``state.parallel_results[source_name]`` and combines
    them into a single unified output.

    Attributes:
        source_name: Name of the ParallelNode whose results to merge.
        merge_mode: How to combine branch results.
            ``"concat"`` — concatenate all outputs.
            ``"best"`` — pick the longest/most substantial output.
            ``"aggregate"`` — structured aggregation of all results.
    """

    def __init__(
        self,
        name: str,
        source_name: str,
        merge_mode: str = "aggregate",
    ) -> None:
        super().__init__(name)
        self.source_name = source_name
        self.merge_mode = merge_mode

    async def execute(self, state: GraphState) -> GraphState:
        """Merge parallel branch results into a unified output."""
        branch_results = state.parallel_results.get(self.source_name, [])

        if not branch_results:
            state.output = "[no parallel results to merge]"
            state.current_node = self.name
            return state

        if self.merge_mode == "concat":
            parts = [
                r.get("output", "") for r in branch_results if r.get("output")
            ]
            state.output = "\n---\n".join(parts)

        elif self.merge_mode == "best":
            best = max(
                branch_results,
                key=lambda r: len(r.get("output", "")),
            )
            state.output = best.get("output", "")

        elif self.merge_mode == "aggregate":
            outputs = []
            for r in branch_results:
                branch_name = r.get("branch", "unknown")
                output = r.get("output", "")
                if output:
                    outputs.append(f"[{branch_name}] {output}")
            state.output = "\n".join(outputs) if outputs else "[all branches completed]"
            state.context["fan_in_count"] = len(branch_results)

        state.current_node = self.name
        return state


# ── StateGraph ────────────────────────────────────────────────────────


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
    - Subgraph nodes (nested StateGraph execution).
    - Parallel nodes (concurrent branch execution).
    - Fan-in nodes (merge parallel results).
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

    # ── Execution ─────────────────────────────────────────────────

    async def run(
        self,
        initial_state: GraphState,
        on_node_complete: Callable[[str, GraphState], Awaitable[None]] | None = None,
        max_steps: int = 100,
    ) -> GraphState:
        """Execute the graph starting from the entry point.

        Walks the graph node by node, resolving conditional edges
        as needed, until no more edges are found (graph ends).

        Args:
            initial_state: The starting state for execution.
            on_node_complete: Optional async callback invoked after each node
                finishes. Receives ``(node_name, state)``.

        Returns:
            The final graph state after all nodes have executed.
        """
        if not self._entry_point:
            raise RuntimeError("Entry point not set — call set_entry_point() first")

        state = initial_state
        state.status = "running"
        # BFS frontier of pending nodes. A linear graph (one outgoing edge per
        # node) dequeues exactly one node at a time — identical to the old
        # single-pointer walk. A fan-out node (multiple outgoing edges) expands
        # the frontier so every branch is reachable (the old ``for-edge-return-
        # first`` loop only ever took the first edge, stranding the rest).
        pending: list[str] = [self._entry_point]
        steps = 0

        while pending:
            current_node_name = pending.pop(0)
            steps += 1
            if steps > max_steps:
                state.errors.append(
                    f"Graph execution exceeded max_steps={max_steps}, possible cycle detected"
                )
                logger.warning(
                    "Graph %s exceeded max_steps=%d at node %s",
                    self.graph_id, max_steps, current_node_name,
                )
                break
            node = self._nodes.get(current_node_name)
            if node is None:
                state.errors.append(f"Node {current_node_name!r} not found")
                continue

            state.current_node = current_node_name
            state = await node.execute(state)

            # Save checkpoint after each node
            if self._checkpoint_store is not None:
                self._checkpoint_counter += 1
                cp_id = f"step-{self._checkpoint_counter}"
                self._checkpoint_store.save(self.graph_id, cp_id, state)

            # Notify listener after each node
            if on_node_complete is not None:
                await on_node_complete(current_node_name, state)

            # Resolve next node(s) — may fan out to multiple targets.
            pending.extend(self._resolve_next(current_node_name, state))

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

        # Resume from the node(s) after the last executed one — fan-out aware.
        pending: list[str] = list(self._resolve_next(state.current_node, state))

        while pending:
            current_node_name = pending.pop(0)
            node = self._nodes.get(current_node_name)
            if node is None:
                state.errors.append(f"Node {current_node_name!r} not found")
                continue

            state.current_node = current_node_name
            state = await node.execute(state)

            if self._checkpoint_store is not None:
                self._checkpoint_counter += 1
                cp_id = f"step-{self._checkpoint_counter}"
                self._checkpoint_store.save(self.graph_id, cp_id, state)

            pending.extend(self._resolve_next(current_node_name, state))

        state.status = "done"
        return state

    # ── Graph introspection ───────────────────────────────────────

    def get_node(self, name: str) -> GraphNode | None:
        """Return a node by name, or None if not found."""
        return self._nodes.get(name)

    def list_nodes(self) -> list[str]:
        """Return all registered node names."""
        return list(self._nodes.keys())

    def list_edges(self) -> list[dict[str, Any]]:
        """Return all edges as structured dictionaries."""
        result: list[dict[str, Any]] = []
        for source, edges in self._edges.items():
            for edge in edges:
                if isinstance(edge, ConditionalEdge):
                    result.append({
                        "source": source,
                        "type": "conditional",
                        "targets": edge.targets,
                    })
                else:
                    result.append({
                        "source": source,
                        "target": edge.target,
                        "type": "unconditional",
                    })
        return result

    def _resolve_next(self, current: str, state: GraphState) -> list[str]:
        """Collect the next node(s) after *current* from ALL outgoing edges.

        The old implementation ``for edge in edges: return edge.route(state)``
        returned on the first edge, so a fan-out node (multiple outgoing
        edges) could never reach any target but the first. We now collect
        every edge's route, de-duplicating and dropping empty routes (e.g. a
        conditional edge's ``__default__`` → ``""`` sentinel that ends a
        branch). A single-edge node returns a one-element list, preserving
        linear-graph behaviour.
        """
        edges = self._edges.get(current, [])
        routes: list[str] = []
        seen: set[str] = set()
        for edge in edges:
            target = edge.route(state)
            if target and target not in seen:
                seen.add(target)
                routes.append(target)
        return routes
