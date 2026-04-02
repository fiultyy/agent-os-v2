import { create } from "zustand";

interface Agent {
  id: string;
  name: string;
  status: "idle" | "running" | "error";
}

interface AgentState {
  agents: Agent[];
  activeAgentId: string | null;
  setAgents: (agents: Agent[]) => void;
  setActiveAgent: (id: string | null) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  agents: [],
  activeAgentId: null,
  setAgents: (agents) => set({ agents }),
  setActiveAgent: (id) => set({ activeAgentId: id }),
}));
