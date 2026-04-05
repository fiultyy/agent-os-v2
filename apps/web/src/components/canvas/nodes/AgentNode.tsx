import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot } from "lucide-react";

/** Agent node with name, status indicator, and memory count. */
export function AgentNode({ data }: NodeProps) {
  const status = String(data.status ?? "idle");
  const memoryCount = Number(data.memoryCount ?? 0);

  const statusColor: Record<string, string> = {
    idle: "bg-gray-400",
    running: "bg-green-500 animate-pulse",
    error: "bg-red-500",
  };

  return (
    <div className="min-w-[160px] rounded-lg border-2 border-blue-500 bg-white px-4 py-3 shadow-md">
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4 text-blue-600" />
        <span className="text-sm font-semibold">{String(data.label ?? "Agent")}</span>
        <span className={`ml-auto h-2.5 w-2.5 rounded-full ${statusColor[status] ?? statusColor.idle}`} />
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="text-xs text-gray-500">Memory: {memoryCount} items</span>
        {data.workingMemoryCount != null && (
          <span className={`text-[10px] font-medium ${
            data.workingMemoryCount < 5 ? "text-green-600" :
            data.workingMemoryCount < 15 ? "text-yellow-600" :
            "text-red-600"
          }`}>
            WM: {String(data.workingMemoryCount)}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
