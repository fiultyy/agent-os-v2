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

// ── LocalStorage persistence ──────────────────────────────────

const STORAGE_KEY = "agent-os-flow-state";
let _saveTimer: ReturnType<typeof setTimeout> | null = null;

function loadFromStorage(): { nodes: Node[]; edges: Edge[] } {
  if (typeof window === "undefined") return { nodes: [], edges: [] };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { nodes: [], edges: [] };
    return JSON.parse(raw);
  } catch {
    return { nodes: [], edges: [] };
  }
}

function saveToStorage(nodes: Node[], edges: Edge[]) {
  if (typeof window === "undefined") return;
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ nodes, edges }));
    } catch {
      // localStorage full or unavailable — silently ignore
    }
  }, 500);
}

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

export const useFlowStore = create<FlowState>((set, get) => {
  const saved = loadFromStorage();
  return {
  nodes: saved.nodes,
  edges: saved.edges,
  selectedNodeId: null,

  onNodesChange: (changes) => {
    const nodes = applyNodeChanges(changes, get().nodes);
    set({ nodes });
    saveToStorage(nodes, get().edges);
  },

  onEdgesChange: (changes) => {
    const edges = applyEdgeChanges(changes, get().edges);
    set({ edges });
    saveToStorage(get().nodes, edges);
  },

  onConnect: (connection: Connection) => {
    const edges = rfAddEdge(connection, get().edges);
    set({ edges });
    saveToStorage(get().nodes, edges);
  },

  setNodes: (nodes) => { set({ nodes }); saveToStorage(nodes, get().edges); },
  setEdges: (edges) => { set({ edges }); saveToStorage(get().nodes, edges); },

  addNode: (node, addAgent) => {
    const nodes = [...get().nodes, node];
    set({ nodes });
    saveToStorage(nodes, get().edges);

    // If it's an agent node, create via API and sync stores
    if (node.type === "agent" && addAgent) {
      const label = String(node.data?.label ?? "New Agent");
      createAgent({ name: label, model: "glm-4-flash" })
        .then((remote) => {
          get().updateNodeData(node.id, { agentId: remote.id, label: remote.name });
          addAgent({
            id: remote.id,
            name: remote.name,
            description: remote.description ?? "",
            status: remote.status ?? "idle",
            model: remote.model ?? "glm-4-flash",
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
            model: "glm-4-flash",
            tools: [],
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          });
        });
    }
  },

  removeNode: (id) => {
    const nodes = get().nodes.filter((n) => n.id !== id);
    const edges = get().edges.filter((e) => e.source !== id && e.target !== id);
    set({ nodes, edges });
    saveToStorage(nodes, edges);
  },

  updateNodeData: (id, data) => {
    const nodes = get().nodes.map((n) =>
      n.id === id ? { ...n, data: { ...n.data, ...data } } : n
    );
    set({ nodes });
    saveToStorage(nodes, get().edges);
  },

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  };
});
