"use client";

import { create } from "zustand";
import {
  getConversations,
  type ConversationItem,
} from "@/lib/api";

// activeConversationId 持久化:刷新后回到上次对话(localStorage 仅记 id,
// 列表仍从后端拉取 —— 与 flowStore 的本地持久化不同,这里只记"当前选哪个")。
const ACTIVE_KEY = "agent-os-active-conversation";

function loadActive(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(ACTIVE_KEY) || null;
  } catch {
    return null;
  }
}

function saveActive(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // localStorage 不可用 — 静默
  }
}

export interface ConversationState {
  conversations: ConversationItem[];
  activeConversationId: string | null;
  loading: boolean;
  setActive: (id: string | null) => void;
  loadConversations: (agentId?: string) => Promise<void>;
  removeConversation: (id: string) => void;
}

export const useConversationStore = create<ConversationState>((set) => ({
  conversations: [],
  activeConversationId: loadActive(),
  loading: false,

  setActive: (id) => {
    saveActive(id);
    set({ activeConversationId: id });
  },

  loadConversations: async (agentId) => {
    set({ loading: true });
    try {
      const list = await getConversations(agentId);
      set({ conversations: list, loading: false });
    } catch {
      set({ loading: false });
    }
  },

  removeConversation: (id) =>
    set((s) => {
      const conversations = s.conversations.filter((c) => c.id !== id);
      const activeConversationId =
        s.activeConversationId === id ? null : s.activeConversationId;
      if (s.activeConversationId === id) saveActive(null);
      return { conversations, activeConversationId };
    }),
}));
