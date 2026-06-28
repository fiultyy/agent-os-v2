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

/** A runtime observation from the introspective observer layer (error_spike / stalled / degraded).
 *  Payload mirrors the backend emit_runtime_observation (snake_case). */
export interface ObservationEvent {
  kind: string;
  agent_id: string;
  timestamp: string;
  session_id?: string;
  errors?: number;
  window?: number;
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
  /** Runtime observations (error_spike/stalled/degraded) from the observer layer. */
  observationEvents: ObservationEvent[];
  /** Currently replaying execution index (-1 = not replaying). */
  replayIndex: number;

  toggleDebugMode: () => void;
  addExecutionEvent: (event: ExecutionEvent) => void;
  addMessage: (msg: CommMessage) => void;
  addMemoryEvent: (event: MemoryEvent) => void;
  addObservationEvent: (event: ObservationEvent) => void;
  setReplayIndex: (idx: number) => void;
  clearHistory: () => void;
}

export const useDebugStore = create<DebugState>((set) => ({
  debugMode: false,
  executionEvents: [],
  messages: [],
  memoryEvents: [],
  observationEvents: [],
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

  addObservationEvent: (event) =>
    set((s) => ({ observationEvents: [...s.observationEvents, event] })),

  setReplayIndex: (idx) => set({ replayIndex: idx }),

  clearHistory: () =>
    set({ executionEvents: [], messages: [], memoryEvents: [], observationEvents: [], replayIndex: -1 }),
}));
