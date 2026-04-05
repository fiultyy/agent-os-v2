"use client";

import { Bot, Wrench, FileText, GripVertical, Brain, Users } from "lucide-react";
import Link from "next/link";
import type { FlowNodeType } from "@/types/flow";

interface PaletteItem {
  type: FlowNodeType;
  label: string;
  icon: React.ReactNode;
  color: string;
}

const palette: PaletteItem[] = [
  { type: "agent", label: "Agent", icon: <Bot className="h-4 w-4" />, color: "text-blue-600 border-blue-400 bg-blue-50" },
  { type: "tool", label: "Tool", icon: <Wrench className="h-4 w-4" />, color: "text-green-600 border-green-400 bg-green-50" },
  { type: "prompt", label: "Prompt", icon: <FileText className="h-4 w-4" />, color: "text-purple-600 border-purple-400 bg-purple-50" },
];

function onDragStart(e: React.DragEvent, type: FlowNodeType) {
  e.dataTransfer.setData("application/reactflow-type", type);
  e.dataTransfer.effectAllowed = "move";
}

export function Sidebar() {
  return (
    <aside className="flex h-full w-56 flex-col border-r bg-gray-50">
      <div className="border-b px-4 py-3 font-semibold text-sm">组件面板</div>
      <div className="flex-1 space-y-1 p-3">
        <p className="mb-2 text-xs text-gray-400">拖拽到画布创建节点</p>
        {palette.map((item) => (
          <div
            key={item.type}
            draggable
            onDragStart={(e) => onDragStart(e, item.type)}
            className={`flex cursor-grab items-center gap-2 rounded-md border px-3 py-2 text-sm transition-shadow hover:shadow-md active:cursor-grabbing ${item.color}`}
          >
            <GripVertical className="h-3 w-3 opacity-40" />
            {item.icon}
            <span className="font-medium">{item.label}</span>
          </div>
        ))}

        <div className="mt-4 border-t pt-3">
          <p className="mb-2 text-xs text-gray-400">快捷面板</p>
          <Link
            href="/agents"
            className="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-white hover:text-gray-900"
          >
            <Users className="h-4 w-4 text-blue-500" />
            <span className="font-medium">Agent 管理</span>
          </Link>
          <Link
            href="/memory"
            className="mt-1 flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-white hover:text-gray-900"
          >
            <Brain className="h-4 w-4 text-purple-500" />
            <span className="font-medium">记忆 & 调试</span>
          </Link>
        </div>
      </div>
    </aside>
  );
}
