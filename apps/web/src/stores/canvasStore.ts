// P1-5: Canvas Store
"use client";
import { create } from "zustand";
import type { CanvasEvent, CanvasTab, Branch, LODLevel } from "@/types/canvas";

const MAX_EVENTS = 2000;

function dedupe(ev: CanvasEvent[]): CanvasEvent[] {
  const seen = new Set<string>();
  return ev.filter(e => { if (seen.has(e.event_id)) return false; seen.add(e.event_id); return true; });
}

export interface CanvasStore {
  sessionId: string | null;
  connected: boolean;
  tabs: CanvasTab[];
  activeTabId: string | null;
  branches: Branch[];
  events: CanvasEvent[];
  ticks: Map<string, import("@/types/canvas").Tick>;
  lod: LODLevel;
  addTab: (branchId: string, label?: string) => CanvasTab;
  removeTab: (tabId: string) => void;
  switchTab: (tabId: string) => void;
  addBranch: (b: Branch) => void;
  removeBranch: (id: string) => void;
  addEvent: (e: CanvasEvent) => void;
  clearEvents: () => void;
  setConnected: (v: boolean) => void;
  setLOD: (l: LODLevel) => void;
}

export const useCanvasStore = create<CanvasStore>((set, get) => ({
  sessionId: null, connected: false, tabs: [], activeTabId: null,
  branches: [], events: [], ticks: new Map(), lod: 2,

  addTab: (branchId, label) => {
    const { tabs, branches } = get();
    const branch = branches.find(b => b.branch_id === branchId);
    const tab: CanvasTab = { tab_id: "tab_" + Date.now(), label: label || "Tab " + (tabs.length + 1), branch_id: branchId, session_id: branch?.session_id || get().sessionId || "" };
    set({ tabs: [...tabs, tab], activeTabId: tab.tab_id });
    return tab;
  },
  removeTab: (tabId) => {
    const { tabs, activeTabId } = get();
    const idx = tabs.findIndex(t => t.tab_id === tabId);
    const newTabs = tabs.filter(t => t.tab_id !== tabId);
    set({ tabs: newTabs, activeTabId: newTabs.length ? (activeTabId === tabId ? newTabs[Math.max(0, idx - 1)].tab_id : activeTabId) : null });
  },
  switchTab: (tabId) => set({ activeTabId: tabId }),
  addBranch: (b) => set(s => ({ branches: s.branches.some(x => x.branch_id === b.branch_id) ? s.branches.map(x => x.branch_id === b.branch_id ? b : x) : [...s.branches, b] })),
  removeBranch: (id) => set(s => ({ branches: s.branches.filter(b => b.branch_id !== id) })),
  addEvent: (e) => set(s => {
    const nextEvents = dedupe([...s.events, e]).slice(-MAX_EVENTS);
    const nextTicks = new Map(s.ticks);
    // Rebuild ticks from tick.started / tick.completed events
    if (e.type === "tick.started") {
      const existing = nextTicks.get(e.tick_id);
      if (!existing) {
        nextTicks.set(e.tick_id, {
          tick_id: e.tick_id,
          parent_tick_id: null,
          branch_id: e.branch_id,
          request: (e.data.request as string) || "",
          response: "",
          summary: "",
          label: "",
          tool_calls: [],
          status: "running" as const,
          created_at: e.timestamp,
          completed_at: null,
        });
      } else {
        nextTicks.set(e.tick_id, { ...existing, request: (e.data.request as string) || existing.request, status: "running" as const, created_at: e.timestamp });
      }
    } else if (e.type === "tick.completed") {
      const existing = nextTicks.get(e.tick_id);
      if (existing) {
        nextTicks.set(e.tick_id, {
          ...existing,
          response: (e.data.response as string) || "",
          status: (e.data.status as "completed" | "error") || "completed",
          completed_at: e.timestamp,
        });
      } else {
        nextTicks.set(e.tick_id, {
          tick_id: e.tick_id,
          parent_tick_id: null,
          branch_id: e.branch_id,
          request: "",
          response: (e.data.response as string) || "",
          summary: "",
          label: "",
          tool_calls: [],
          status: (e.data.status as "completed" | "error") || "completed",
          created_at: e.timestamp,
          completed_at: e.timestamp,
        });
      }
    } else if (e.type === "tool.call" && e.tick_id) {
      let existing = nextTicks.get(e.tick_id);
      if (!existing) {
        // Stub tick for out-of-order events (tool.call before tick.started)
        existing = {
          tick_id: e.tick_id,
          parent_tick_id: null,
          branch_id: e.branch_id,
          request: "",
          response: "",
          summary: "",
          label: "",
          tool_calls: [],
          status: "running" as const,
          created_at: e.timestamp,
          completed_at: null,
        };
        nextTicks.set(e.tick_id, existing);
      }
      const tc: import("@/types/canvas").ToolCallInfo = {
        tool_call_id: (e.data.tool_call_id as string) || (e.data.call_id as string) || "",
        tool_name: (e.data.tool_name as string) || "",
        label: (e.data.tool_name as string) || "",
        summary: "",
        args: JSON.stringify(e.data.arguments ?? {}),
        result: "",
        status: "running" as const,
      };
      nextTicks.set(e.tick_id, { ...existing, tool_calls: [...existing.tool_calls, tc] });
    } else if (e.type === "tool.result" && e.tick_id) {
      const existing = nextTicks.get(e.tick_id);
      if (existing) {
        const callId = (e.data.tool_call_id as string) || (e.data.call_id as string) || "";
        const updatedCalls = existing.tool_calls.map(tc =>
          tc.tool_call_id === callId
            ? { ...tc, result: (e.data.result as string) || "", status: "completed" as const }
            : tc
        );
        nextTicks.set(e.tick_id, { ...existing, tool_calls: updatedCalls });
      }
    }
    return { events: nextEvents, ticks: nextTicks };
  }),
  clearEvents: () => set({ events: [], ticks: new Map() }),
  setConnected: (v) => set({ connected: v }),
  setLOD: (lod) => set({ lod }),
}));
