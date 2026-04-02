import { Handle, Position, type NodeProps } from "@xyflow/react";

export function AgentNode({ data }: NodeProps) {
  return (
    <div className="rounded-lg border-2 border-blue-500 bg-white px-4 py-2 shadow-md">
      <Handle type="target" position={Position.Top} />
      <div className="text-sm font-semibold">{String(data.label ?? "Agent")}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
