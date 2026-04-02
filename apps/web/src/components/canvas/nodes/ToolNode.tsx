import { Handle, Position, type NodeProps } from "@xyflow/react";

export function ToolNode({ data }: NodeProps) {
  return (
    <div className="rounded-lg border-2 border-green-500 bg-white px-4 py-2 shadow-md">
      <Handle type="target" position={Position.Top} />
      <div className="text-sm font-semibold">{String(data.label ?? "Tool")}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
