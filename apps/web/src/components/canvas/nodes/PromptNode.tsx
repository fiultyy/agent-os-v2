import { Handle, Position, type NodeProps } from "@xyflow/react";
import { FileText } from "lucide-react";

export function PromptNode({ data }: NodeProps) {
  const variables: string[] = Array.isArray(data.variables)
    ? (data.variables as string[])
    : [];

  return (
    <div className="min-w-[160px] rounded-lg border-2 border-purple-500 bg-white px-4 py-3 shadow-md">
      <Handle type="target" position={Position.Top} />
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-purple-600" />
        <span className="text-sm font-semibold">
          {String(data.label ?? "Prompt")}
        </span>
      </div>
      {variables.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {variables.map((v) => (
            <span
              key={v}
              className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] text-purple-700"
            >
              {`{{${v}}}`}
            </span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
