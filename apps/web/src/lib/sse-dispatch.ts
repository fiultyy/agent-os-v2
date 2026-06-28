import type { CommMessage, MemoryEvent, ObservationEvent } from "@/stores/debugStore";

/** SSE event shape emitted by executeWithSSE / orchestrateWithSSE. */
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

/** Optional debug-store hooks the dispatcher forwards store-bound events to. */
export interface SSEDispatchDeps {
  addMemoryEvent?: (e: MemoryEvent) => void;
  addMessage?: (m: CommMessage) => void;
  addObservationEvent?: (o: ObservationEvent) => void;
}

/**
 * Forward store-bound SSE events to the debug store. Node lifecycle
 * (node_start / node_complete), execution_complete and error events stay local
 * to each caller (they drive component-specific UI like message bubbles or
 * node logs), so they are intentionally NOT handled here.
 *
 * Centralising the store-bound forwarding means a new store-bound event (e.g.
 * ``runtime_observation`` from the introspective observer layer) only needs one
 * new case here, and every SSE entry point (page / ExecutePanel /
 * OrchestrationPanel) picks it up automatically.
 */
export function dispatchSSEEvent(event: SSEEvent, deps: SSEDispatchDeps): void {
  switch (event.event) {
    case "memory_event":
      deps.addMemoryEvent?.(event.data as unknown as MemoryEvent);
      break;
    case "agent_message":
      deps.addMessage?.(event.data as unknown as CommMessage);
      break;
    case "runtime_observation":
      deps.addObservationEvent?.(event.data as unknown as ObservationEvent);
      break;
    default:
      // node_start / node_complete / execution_complete / error: handled locally.
      break;
  }
}
