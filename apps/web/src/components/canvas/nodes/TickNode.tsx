"use client";

import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Play, CheckCircle2, XCircle } from "lucide-react";
import type { Tick, LODLevel } from "@/types/canvas";

// ── Status icon ───────────────────────────────────────────────

function StatusIcon({ status }: { status: Tick["status"] }) {
  if (status === "completed") return <CheckCircle2 className="h-4 w-4 text-green-500" />;
  if (status === "error") return <XCircle className="h-4 w-4 text-red-500" />;
  return <Play className="h-4 w-4 animate-pulse text-blue-500" />;
}

// ── LOD sub-components ────────────────────────────────────────

function TickNodeL1({ tick }: { tick: Tick }) {
  const toolNames = tick.tool_calls.map((t) => t.label).join(", ") || "—";
  return (
    <div className="min-w-[140px] rounded-xl border-2 border-blue-400 bg-white px-4 py-3 shadow-lg">
      <div className="flex items-center gap-2">
        <StatusIcon status={tick.status} />
        <span className="text-sm font-bold text-gray-800">{tick.label || "Tick"}</span>
      </div>
      <div className="mt-1 text-xs text-gray-500">tools: {toolNames}</div>
    </div>
  );
}

function TickNodeL2({ tick }: { tick: Tick }) {
  return (
    <div className="min-w-[200px] rounded-xl border-2 border-blue-400 bg-white px-4 py-3 shadow-lg">
      <div className="flex items-center gap-2">
        <StatusIcon status={tick.status} />
        <span className="text-sm font-bold text-gray-800">{tick.label || "Tick"}</span>
      </div>
      <div className="mt-2 rounded bg-gray-50 px-2 py-1">
        <div className="mb-1 text-[10px] font-semibold uppercase text-gray-400">Request</div>
        <div className="line-clamp-2 text-xs text-gray-700">{tick.request || "—"}</div>
      </div>
      <div className="mt-1 rounded bg-gray-50 px-2 py-1">
        <div className="mb-1 text-[10px] font-semibold uppercase text-gray-400">Summary</div>
        <div className="line-clamp-2 text-xs text-gray-700">{tick.summary || "—"}</div>
      </div>
      {tick.tool_calls.length > 0 && (
        <div className="mt-1 text-xs text-gray-500">
          {tick.tool_calls.length} tool(s): {tick.tool_calls.map((t) => t.label).join(", ")}
        </div>
      )}
    </div>
  );
}

function TickNodeL3({ tick }: { tick: Tick }) {
  return (
    <div className="min-w-[280px] rounded-xl border-2 border-blue-400 bg-white px-4 py-3 shadow-lg">
      <div className="flex items-center gap-2">
        <StatusIcon status={tick.status} />
        <span className="text-sm font-bold text-gray-800">{tick.label || "Tick"}</span>
        <span className="ml-auto text-[10px] text-gray-400">L3</span>
      </div>
      <div className="mt-2 rounded bg-blue-50 px-2 py-1">
        <div className="mb-1 text-[10px] font-semibold uppercase text-blue-400">Request</div>
        <div className="max-h-24 overflow-y-auto text-xs text-gray-700">{tick.request || "—"}</div>
      </div>
      <div className="mt-1 rounded bg-green-50 px-2 py-1">
        <div className="mb-1 text-[10px] font-semibold uppercase text-green-400">Response</div>
        <div className="max-h-24 overflow-y-auto text-xs text-gray-700">{tick.response || "—"}</div>
      </div>
      {tick.tool_calls.length > 0 && (
        <div className="mt-2 flex flex-col gap-1">
          <div className="text-[10px] font-semibold uppercase text-gray-400">
            Tool Calls ({tick.tool_calls.length})
          </div>
          {tick.tool_calls.map((tool) => (
            <div key={tool.tool_call_id} className="rounded bg-gray-50 px-2 py-1 text-xs">
              <span className="font-medium text-gray-700">{tool.label}</span>
              <div className="mt-0.5 text-gray-400">{tool.summary}</div>
              <div className="mt-0.5 text-[10px] text-gray-500">args: {tool.args}</div>
              <div className="mt-0.5 text-[10px] text-gray-500">result: {tool.result}</div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 flex gap-2 text-[10px] text-gray-400">
        <span>created: {tick.created_at}</span>
        {tick.completed_at && <span>completed: {tick.completed_at}</span>}
      </div>
    </div>
  );
}

// ── TickNode ───────────────────────────────────────────────────

export interface TickNodeData {
  tick: Tick;
  lod: LODLevel;
  onClick?: (tickId: string) => void;
}

/** Tick node for D-31 Canvas — supports LOD 1/2/3 rendering */
export const TickNode = memo(function TickNode(props: {
  data: TickNodeData;
  id: string;
  dragging?: boolean;
}) {
  const { tick, lod } = props.data;

  return (
    <>
      <Handle type="target" position={Position.Top} />
      {lod === 1 && <TickNodeL1 tick={tick} />}
      {lod === 2 && <TickNodeL2 tick={tick} />}
      {lod === 3 && <TickNodeL3 tick={tick} />}
      <Handle type="source" position={Position.Bottom} />
    </>
  );
});