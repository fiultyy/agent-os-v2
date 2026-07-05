import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Wrench } from "lucide-react";

/** Tool node with name, last execution status, and duration. */
export const ToolNode = memo(function ToolNode({ data }: NodeProps) {
  const lastStatus = String(data.lastStatus ?? "idle");
  const duration = Number(data.duration ?? 0);

  // Match backend status: completed→green, failed→red, running→yellow
  const statusColor: Record<string, string> = {
    idle: "text-gray-400",
    running: "text-yellow-600",
    completed: "text-green-600",
    success: "text-green-600",
    failed: "text-red-600",
    error: "text-red-600",
    cancelled: "text-gray-500",
    pending: "text-gray-400",
  };

  return (
    <div className="relative min-w-[160px] rounded-lg border-2 border-green-500 bg-white px-4 py-3 shadow-md">
      <Handle type="target" position={Position.Top} />
      <span className="absolute right-2 top-2 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-medium text-amber-700">演示</span>
      <div className="flex items-center gap-2">
        <Wrench className="h-4 w-4 text-green-600" />
        <span className="text-sm font-semibold">{String(data.label ?? "Tool")}</span>
      </div>
      <div className={`mt-1 text-xs ${statusColor[lastStatus] ?? statusColor.idle}`}>
        {lastStatus === "idle" ? "Ready" : `${lastStatus}${duration > 0 ? ` (${duration}ms)` : ""}`}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});
