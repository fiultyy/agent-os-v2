import { create } from "zustand";
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type Connection,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge as rfAddEdge,
} from "@xyflow/react";
import { createAgent } from "@/lib/api";
import type { AgentItem } from "./agentStore";

export interface FlowState {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
  addNode: (node: Node, addAgent?: (agent: AgentItem) => void) => void;
  removeNode: (id: string) => void;
  updateNodeData: (id: string, data: Record<string, unknown>) => void;
  selectedNodeId: string | null;
  setSelectedNodeId: (id: string | null) => void;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,

  onNodesChange: (changes) => {
    set({ nodes: applyNodeChanges(changes, get().nodes) });
  },

  onEdgesChange: (changes) => {
    set({ edges: applyEdgeChanges(changes, get().edges) });
  },

  onConnect: (connection: Connection) => {
    set({ edges: rfAddEdge(connection, get().edges) });
  },

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),

  addNode: (node, addAgent) => {
    set((s) => ({ nodes: [...s.nodes, node] }));

    // If it's an agent node, create via API and sync stores
    if (node.type === "agent" && addAgent) {
      const label = String(node.data?.label ?? "New Agent");
      createAgent({ name: label, model: "gpt-4o-mini" })
        .then((remote) => {
          get().updateNodeData(node.id, { agentId: remote.id, label: remote.name });
          addAgent({
            id: remote.id,
            name: remote.name,
            description: remote.description ?? "",
            status: remote.status ?? "idle",
            model: remote.model ?? "gpt-4o-mini",
            tools: remote.tools ?? [],
            createdAt: remote.createdAt ?? new Date().toISOString(),
            updatedAt: remote.updatedAt ?? new Date().toISOString(),
          });
        })
        .catch(() => {
          // Fallback: still add locally even if API fails
          addAgent({
            id: node.id,
            name: label,
            description: "",
            status: "idle",
            model: "gpt-4o-mini",
            tools: [],
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          });
        });
    }
  },

  removeNode: (id) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      edges: s.edges.filter((e) => e.source !== id && e.target !== id),
    })),

  updateNodeData: (id, data) =>
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...data } } : n
      ),
    })),

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
}));
