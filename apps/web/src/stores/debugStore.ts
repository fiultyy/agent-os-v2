import { create } from "zustand";

/** A single message exchanged between agents. */
export interface CommMessage {
  id: string;
  senderId: string;
  recipientId: string | null;
  sessionId: string;
  workspaceId: string;
  messageType: string;
  content: string;
  payload: Record<string, unknown>;
  correlationId: string | null;
  timestamp: string;
  priority: number;
  deliveryStatus: string;
}

/** A single node execution event for debug/replay. */
export interface ExecutionEvent {
  id: string;
  nodeId: string;
  agentId: string;
  status: "running" | "done" | "error";
  input: string;
  output: string;
  executionTimeMs: number;
  timestamp: string;
  metadata: Record<string, unknown>;
}

/** A single memory lifecycle event (compress / forget / migrate). */
export interface MemoryEvent {
  event: "compress" | "forget" | "migrate";
  agentId?: string;
  level?: string;
  path?: string;
  originalCount?: number;
  retainedCount?: number;
  summaryCount?: number;
  scanned?: number;
  archived?: number;
  count?: number;
  timestamp?: string;
}

interface DebugState {
  /** Whether debug mode is active. */
  debugMode: boolean;
  /** Execution events for history/replay. */
  executionEvents: ExecutionEvent[];
  /** Communication messages. */
  messages: CommMessage[];
  /** Memory lifecycle events (compress/forget/migrate). */
  memoryEvents: MemoryEvent[];
  /** Currently replaying execution index (-1 = not replaying). */
  replayIndex: number;

  toggleDebugMode: () => void;
  addExecutionEvent: (event: ExecutionEvent) => void;
  addMessage: (msg: CommMessage) => void;
  addMemoryEvent: (event: MemoryEvent) => void;
  setReplayIndex: (idx: number) => void;
  clearHistory: () => void;
}

export const useDebugStore = create<DebugState>((set) => ({
  debugMode: false,
  executionEvents: [],
  messages: [],
  memoryEvents: [],
  replayIndex: -1,

  toggleDebugMode: () => set((s) => ({ debugMode: !s.debugMode })),

  addExecutionEvent: (event) =>
    set((s) => ({ executionEvents: [...s.executionEvents, event] })),

  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),

  addMemoryEvent: (event) =>
    set((s) => ({
      memoryEvents: [...s.memoryEvents, { ...event, timestamp: new Date().toISOString() }],
    })),

  setReplayIndex: (idx) => set({ replayIndex: idx }),

  clearHistory: () => set({ executionEvents: [], messages: [], memoryEvents: [], replayIndex: -1 }),
}));
