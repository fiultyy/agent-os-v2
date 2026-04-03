import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Wrench } from "lucide-react";

/** Tool node with name, last execution status, and duration. */
export function ToolNode({ data }: NodeProps) {
  const lastStatus = String(data.lastStatus ?? "idle");
  const duration = Number(data.duration ?? 0);

  const statusColor: Record<string, string> = {
    idle: "text-gray-400",
    success: "text-green-600",
    error: "text-red-600",
  };

  return (
    <div className="min-w-[160px] rounded-lg border-2 border-green-500 bg-white px-4 py-3 shadow-md">
      <Handle type="target" position={Position.Top} />
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
}
