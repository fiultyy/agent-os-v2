import { create } from "zustand";

export interface AgentItem {
  id: string;
  name: string;
  description: string;
  status: "idle" | "running" | "error" | "stopped";
  model: string;
  tools: string[];
  createdAt: string;
  updatedAt: string;
}

interface AgentState {
  agents: AgentItem[];
  activeAgentId: string | null;
  setAgents: (agents: AgentItem[]) => void;
  addAgent: (agent: AgentItem) => void;
  removeAgent: (id: string) => void;
  setActiveAgent: (id: string | null) => void;
  updateAgentStatus: (id: string, status: AgentItem["status"]) => void;
  updateAgent: (id: string, updates: Partial<AgentItem>) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  agents: [],
  activeAgentId: null,

  setAgents: (agents) => set({ agents }),

  addAgent: (agent) => set((s) => ({ agents: [...s.agents, agent] })),

  removeAgent: (id) =>
    set((s) => ({ agents: s.agents.filter((a) => a.id !== id) })),

  setActiveAgent: (id) => set({ activeAgentId: id }),

  updateAgentStatus: (id, status) =>
    set((s) => ({
      agents: s.agents.map((a) =>
        a.id === id ? { ...a, status, updatedAt: new Date().toISOString() } : a
      ),
    })),

  updateAgent: (id, updates) =>
    set((s) => ({
      agents: s.agents.map((a) =>
        a.id === id
          ? { ...a, ...updates, updatedAt: new Date().toISOString() }
          : a
      ),
    })),
}));
