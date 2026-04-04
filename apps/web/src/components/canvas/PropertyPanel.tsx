"use client";

import { useFlowStore } from "@/stores/flowStore";
import { X } from "lucide-react";

export function PropertyPanel() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const setSelectedNodeId = useFlowStore((s) => s.setSelectedNodeId);

  const node = nodes.find((n) => n.id === selectedNodeId);
  if (!node) return null;

  const data = node.data as Record<string, unknown>;
  const label = String(data.label ?? "");
  const description = String(data.description ?? "");

  return (
    <div className="flex h-full w-72 flex-col border-l bg-white">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <span className="text-sm font-semibold">
          {node.type === "agent" ? "Agent" : node.type === "tool" ? "Tool" : "Prompt"} 属性
        </span>
        <button
          onClick={() => setSelectedNodeId(null)}
          className="text-gray-400 hover:text-gray-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            节点 ID
          </label>
          <div className="rounded bg-gray-50 px-2 py-1.5 text-xs text-gray-400">
            {node.id}
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            名称
          </label>
          <input
            className="w-full rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            value={label}
            onChange={(e) => updateNodeData(node.id, { label: e.target.value })}
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">
            描述
          </label>
          <textarea
            className="w-full rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            rows={3}
            value={description}
            placeholder="可选描述..."
            onChange={(e) =>
              updateNodeData(node.id, { description: e.target.value })
            }
          />
        </div>

        {node.type === "agent" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">
                状态
              </label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.status ?? "idle")}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">
                Memory 条目
              </label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.memoryCount ?? 0)}
              </div>
            </div>
          </>
        )}

        {node.type === "tool" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">
                最近状态
              </label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.lastStatus ?? "idle")}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">
                耗时 (ms)
              </label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.duration ?? 0)}
              </div>
            </div>
          </>
        )}

        {node.type === "prompt" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-500">
              模板变量（逗号分隔）
            </label>
            <input
              className="w-full rounded border px-2 py-1.5 text-sm focus:border-purple-500 focus:outline-none"
              value={(data.variables as string[])?.join(", ") ?? ""}
              placeholder="topic, style"
              onChange={(e) =>
                updateNodeData(node.id, {
                  variables: e.target.value
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                })
              }
            />
          </div>
        )}

        <div className="pt-4">
          <label className="mb-1 block text-xs font-medium text-gray-500">
            位置
          </label>
          <div className="flex gap-2 text-xs text-gray-400">
            <span>x: {Math.round(node.position.x)}</span>
            <span>y: {Math.round(node.position.y)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
