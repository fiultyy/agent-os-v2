"use client";

// TODO(V2): Add event history timeline panel showing tick-level execution trace
// with expand/collapse, LOD switching, and branch comparison.

import { useState } from "react";
import { useFlowStore } from "@/stores/flowStore";
import { useAgentStore } from "@/stores/agentStore";
import { useDebugStore } from "@/stores/debugStore";
import { executeWithSSE } from "@/lib/api";
import { dispatchSSEEvent } from "@/lib/sse-dispatch";
import { Play, Loader2 } from "lucide-react";

export function ExecutePanel() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);

  const [input, setInput] = useState("");
  const [executing, setExecuting] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const addMemoryEvent = useDebugStore((s) => s.addMemoryEvent);
  const addMessage = useDebugStore((s) => s.addMessage);
  const addObservationEvent = useDebugStore((s) => s.addObservationEvent);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);

  if (!selectedNode || selectedNode.type !== "agent") return null;

  const agentName = String(selectedNode.data.label ?? "Agent");

  async function handleExecute() {
    if (!input.trim() || executing) return;
    setExecuting(true);
    setLogs([]);
    updateNodeData(selectedNode!.id, { status: "running" });

    try {
      await executeWithSSE(
        selectedNode!.id,
        input,
        undefined,
        (event) => {
          const { event: type, data } = event;
          // Forward store-bound events (memory / agent-message / runtime-observation).
          dispatchSSEEvent(event, { addMemoryEvent, addMessage, addObservationEvent });
          const msg = type === "node_complete"
            ? `[${data.node}] ${data.output ?? "done"}`
            : type === "execution_complete"
            ? `Done: ${data.output ?? ""}`
            : type === "error"
            ? `Error: ${data.message ?? ""}`
            : null;
          if (msg) {
            setLogs((prev) => [...prev, msg]);
          }
          // Update memory count
          if (type === "execution_complete") {
            updateNodeData(selectedNode!.id, {
              status: "idle",
              memoryCount: Number(data.memory_count ?? 0),
            });
          }
        }
      );
    } catch (err) {
      setLogs((prev) => [...prev, `Error: ${err}`]);
      updateNodeData(selectedNode!.id, { status: "error" });
    } finally {
      setExecuting(false);
      setInput("");
    }
  }

  return (
    <div className="border-t bg-white p-3">
      <div className="mb-2 text-xs font-semibold text-gray-500">
        执行: {agentName}
      </div>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          placeholder="输入消息..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleExecute()}
          disabled={executing}
        />
        <button
          onClick={handleExecute}
          disabled={executing || !input.trim()}
          className="flex items-center gap-1 rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {executing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          执行
        </button>
      </div>
      {logs.length > 0 && (
        <div className="mt-2 max-h-32 overflow-y-auto rounded bg-gray-50 p-2 text-xs font-mono">
          {logs.map((log, i) => (
            <div key={i} className="text-gray-600">
              {log}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
