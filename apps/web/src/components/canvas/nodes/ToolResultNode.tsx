"use client";

import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Wrench, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import type { ToolCallInfo, LODLevel } from "@/types/canvas";

// ── Status icon ───────────────────────────────────────────────

function ToolStatusIcon({ status }: { status: ToolCallInfo["status"] }) {
  if (status === "completed") return <CheckCircle2 className="h-3 w-3 text-green-500" />;
  if (status === "error") return <XCircle className="h-3 w-3 text-red-500" />;
  if (status === "running") return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />;
  return <Wrench className="h-3 w-3 text-gray-400" />;
}

// ── LOD sub-components ────────────────────────────────────────

function ToolResultNodeL1({ tool }: { tool: ToolCallInfo }) {
  return (
    <div className="flex min-w-[100px] items-center gap-1.5 rounded-lg border border-emerald-400 bg-emerald-50 px-3 py-2 shadow-sm">
      <ToolStatusIcon status={tool.status} />
      <span className="text-xs font-medium text-emerald-700">{tool.label || tool.tool_name}</span>
    </div>
  );
}

function ToolResultNodeL2({ tool }: { tool: ToolCallInfo }) {
  return (
    <div className="flex min-w-[160px] flex-col gap-1 rounded-lg border border-emerald-400 bg-white px-3 py-2 shadow-sm">
      <div className="flex items-center gap-1.5">
        <ToolStatusIcon status={tool.status} />
        <span className="text-xs font-semibold text-emerald-700">{tool.label || tool.tool_name}</span>
      </div>
      <div className="line-clamp-2 text-[11px] text-gray-500">{tool.summary || "—"}</div>
    </div>
  );
}

function ToolResultNodeL3({ tool }: { tool: ToolCallInfo }) {
  return (
    <div className="flex min-w-[220px] flex-col gap-1.5 rounded-lg border border-emerald-400 bg-white px-3 py-2 shadow-sm">
      <div className="flex items-center gap-1.5">
        <ToolStatusIcon status={tool.status} />
        <span className="text-xs font-semibold text-emerald-700">{tool.label || tool.tool_name}</span>
        <span className="ml-auto text-[10px] text-gray-400">L3</span>
      </div>
      <div className="rounded bg-gray-50 px-2 py-1">
        <div className="mb-0.5 text-[9px] font-semibold uppercase text-gray-400">Args</div>
        <div className="max-h-16 overflow-y-auto text-[10px] text-gray-600">{tool.args || "—"}</div>
      </div>
      <div className="rounded bg-gray-50 px-2 py-1">
        <div className="mb-0.5 text-[9px] font-semibold uppercase text-gray-400">Result</div>
        <div className="max-h-16 overflow-y-auto text-[10px] text-gray-600">{tool.result || "—"}</div>
      </div>
    </div>
  );
}

// ── ToolResultNode ─────────────────────────────────────────────

export interface ToolResultNodeData {
  tool: ToolCallInfo;
  lod: LODLevel;
}

/** Tool result node for D-31 Canvas — displays tool call info at LOD 1/2/3 */
export const ToolResultNode = memo(function ToolResultNode(props: {
  data: ToolResultNodeData;
  id: string;
  dragging?: boolean;
}) {
  const { tool, lod } = props.data;

  return (
    <>
      <Handle type="target" position={Position.Top} />
      {lod === 1 && <ToolResultNodeL1 tool={tool} />}
      {lod === 2 && <ToolResultNodeL2 tool={tool} />}
      {lod === 3 && <ToolResultNodeL3 tool={tool} />}
      <Handle type="source" position={Position.Bottom} />
    </>
  );
});