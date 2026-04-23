// P2-1: Layer 2 Store
"use client";
import { create } from "zustand";
import type { Layer2Node, Layer2NodeType } from "@/types/canvas";

export interface Layer2SubmitPayload {
  type: "layer2.submit";
  session_id: string;
  branch_id: string;
  nodes: Array<{ type: Layer2NodeType; content: string }>;
}

interface Layer2State {
  staging: Layer2Node[];
  addNode: (type: Layer2NodeType, content: string) => void;
  removeNode: (id: string) => void;
  reorder: (from: number, to: number) => void;
  clear: () => void;
  submitPayload: (sessionId: string, branchId: string) => Layer2SubmitPayload;
}

export const useLayer2Store = create<Layer2State>((set, get) => ({
  staging: [],
  addNode: (type, content) => {
    const { staging } = get();
    const node: Layer2Node = { id: "l2_" + Date.now(), type, content, order: staging.length, created_at: new Date().toISOString() };
    set({ staging: [...staging, node] });
  },
  removeNode: (id) => set(s => ({ staging: s.staging.filter(n => n.id !== id).map((n, i) => ({ ...n, order: i })) })),
  reorder: (from, to) => {
    const arr = [...get().staging];
    const [moved] = arr.splice(from, 1);
    arr.splice(to, 0, moved);
    set({ staging: arr.map((n, i) => ({ ...n, order: i })) });
  },
  clear: () => set({ staging: [] }),
  submitPayload: (sessionId, branchId) => ({
    type: "layer2.submit" as const, session_id: sessionId, branch_id: branchId,
    nodes: get().staging.sort((a, b) => a.order - b.order).map(n => ({ type: n.type, content: n.content })),
  }),
}));
