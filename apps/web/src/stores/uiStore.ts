import { create } from "zustand";

interface UIState {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  selectedPanel: "agents" | "prompts" | "conversations" | "resources" | null;
  setSelectedPanel: (panel: UIState["selectedPanel"]) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  selectedPanel: null,
  setSelectedPanel: (panel) => set({ selectedPanel: panel }),
}));
