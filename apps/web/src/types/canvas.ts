/**
 * D-31 Endless Canvas types
 * Layer 1: Observability (read-only tick event stream)
 * Layer 2: Control (pre-built staging nodes)
 */

// ── LOD Levels ──────────────────────────────────────────────
export type LODLevel = 1 | 2 | 3;

// ── Tick Events (Event Sourcing) ────────────────────────────
export type TickEventType =
  | "tick.started"
  | "token.delta"
  | "tool.call"
  | "tool.result"
  | "tick.completed";

export type BranchEventType =
  | "branch.created"
  | "branch.merged"
  | "branch.pruned";

export type ObservabilityEventType =
  | "scoring.signal"
  | "committee.vote";

export type CanvasEventType =
  | TickEventType
  | BranchEventType
  | ObservabilityEventType;

export interface CanvasEvent {
  event_id: string;
  session_id: string;
  branch_id: string;
  tick_id: string;
  type: CanvasEventType;
  data: Record<string, unknown>;
  lod: LODLevel;
  timestamp: string;
}

// ── Tick Model ──────────────────────────────────────────────
export interface Tick {
  tick_id: string;
  parent_tick_id: string | null;
  branch_id: string;
  /** Full request text (L3) */
  request: string;
  /** Full response text (L3) */
  response: string;
  /** L2 summary */
  summary: string;
  /** L1 label */
  label: string;
  tool_calls: ToolCallInfo[];
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "error";
  created_at: string;
  completed_at: string | null;
}

export interface ToolCallInfo {
  tool_call_id: string;
  tool_name: string;
  /** L1 display */
  label: string;
  /** L2 summary */
  summary: string;
  /** L3 full args */
  args: string;
  /** L3 full result */
  result: string;
  status: "pending" | "running" | "completed" | "error";
}

// ── Branch / Tab / Session ──────────────────────────────────
export interface Branch {
  branch_id: string;
  session_id: string;
  parent_branch_id: string;
  fork_tick_id: string | null;
  status: "active" | "merged" | "pruned" | "archived";
  created_at: string;
  merged_at: string | null;
  metadata?: Record<string, Record<string, unknown>>;
}

export interface CanvasTab {
  tab_id: string;
  label: string;
  branch_id: string;
  session_id: string;
  /** Viewport position for restoration */
  viewport?: { x: number; y: number; zoom: number };
}

// ── Layer 2: Control ────────────────────────────────────────
export type Layer2NodeType = "text" | "command";

export interface Layer2Node {
  id: string;
  type: Layer2NodeType;
  content: string;
  order: number;
  created_at: string;
}

// ── Canvas Layout ───────────────────────────────────────────
export interface CanvasLayout {
  /** Horizontal spacing between ticks on main path */
  tickX: number;
  /** Vertical position of main time axis */
  tickY: number;
  /** Horizontal offset for tool result nodes */
  toolX: number;
  /** Vertical spacing for tool forks */
  toolY: number;
  /** Horizontal offset for parallel branches */
  branchOffsetX: number;
}

export const DEFAULT_LAYOUT: CanvasLayout = {
  tickX: 280,
  tickY: 100,
  toolX: 0,
  toolY: 180,
  branchOffsetX: 400,
};
