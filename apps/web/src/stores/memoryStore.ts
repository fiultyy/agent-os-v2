import { create } from "zustand";

/** Represents a single memory item from the backend. */
export interface MemoryItem {
  id: string;
  agentId: string;
  sessionId: string;
  memoryType: "working" | "session" | "episodic" | "semantic";
  scope: "agent" | "session" | "workspace" | "global";
  content: string;
  importance: number;
  metadata: Record<string, unknown>;
  createdAt: string;
  accessedAt: string;
  archived: boolean;
}

/** Memory layer stats for an agent. */
export interface MemoryLayerStats {
  working: number;
  session: number;
  episodic: number;
  semantic: number;
}

interface MemoryState {
  /** All loaded memory items. */
  items: MemoryItem[];
  /** Currently selected agent filter. */
  selectedAgentId: string | null;
  /** Currently selected memory type filter. */
  selectedType: string | null;
  /** Layer counts for the selected agent. */
  layerStats: MemoryLayerStats;
  /** Loading state. */
  loading: boolean;

  setItems: (items: MemoryItem[]) => void;
  setSelectedAgentId: (id: string | null) => void;
  setSelectedType: (type: string | null) => void;
  setLoading: (loading: boolean) => void;
  computeLayerStats: () => void;
}

export const useMemoryStore = create<MemoryState>((set, get) => ({
  items: [],
  selectedAgentId: null,
  selectedType: null,
  layerStats: { working: 0, session: 0, episodic: 0, semantic: 0 },
  loading: false,

  setItems: (items) => {
    set({ items });
    get().computeLayerStats();
  },

  setSelectedAgentId: (id) => {
    set({ selectedAgentId: id });
    get().computeLayerStats();
  },

  setSelectedType: (type) => set({ selectedType: type }),

  setLoading: (loading) => set({ loading }),

  computeLayerStats: () => {
    const { items, selectedAgentId } = get();
    const filtered = selectedAgentId
      ? items.filter((m) => m.agentId === selectedAgentId)
      : items;

    const stats: MemoryLayerStats = { working: 0, session: 0, episodic: 0, semantic: 0 };
    for (const item of filtered) {
      if (item.memoryType in stats && !item.archived) {
        stats[item.memoryType as keyof MemoryLayerStats]++;
      }
    }
    set({ layerStats: stats });
  },
}));
