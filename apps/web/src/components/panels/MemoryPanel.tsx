"use client";

import { useEffect, useMemo } from "react";
import { useMemoryStore } from "@/stores/memoryStore";
import { useAgentStore } from "@/stores/agentStore";
import { Brain, Clock, Star, Tag, Filter } from "lucide-react";

const LAYER_LABELS: Record<string, string> = {
  working: "L0 Working",
  session: "L1 Session",
  episodic: "L2 Episodic",
  semantic: "L3 Semantic",
};

const LAYER_COLORS: Record<string, string> = {
  working: "bg-yellow-100 text-yellow-800",
  session: "bg-blue-100 text-blue-800",
  episodic: "bg-purple-100 text-purple-800",
  semantic: "bg-green-100 text-green-800",
};

const SCOPE_COLORS: Record<string, string> = {
  agent: "bg-gray-100 text-gray-700",
  session: "bg-indigo-100 text-indigo-700",
  workspace: "bg-cyan-100 text-cyan-700",
  global: "bg-orange-100 text-orange-700",
};

export function MemoryPanel() {
  const items = useMemoryStore((s) => s.items);
  const layerStats = useMemoryStore((s) => s.layerStats);
  const selectedAgentId = useMemoryStore((s) => s.selectedAgentId);
  const selectedType = useMemoryStore((s) => s.selectedType);
  const setSelectedAgentId = useMemoryStore((s) => s.setSelectedAgentId);
  const setSelectedType = useMemoryStore((s) => s.setSelectedType);
  const agents = useAgentStore((s) => s.agents);

  const filteredItems = useMemo(() => {
    let result = items;
    if (selectedAgentId) {
      result = result.filter((m) => m.agentId === selectedAgentId);
    }
    if (selectedType) {
      result = result.filter((m) => m.memoryType === selectedType);
    }
    return result.filter((m) => !m.archived);
  }, [items, selectedAgentId, selectedType]);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Brain className="h-4 w-4 text-purple-600" />
          Memory 面板
        </div>
        <p className="mt-1 text-xs text-gray-500">
          查看 Agent 的记忆层次和内容
        </p>
      </div>

      {/* Layer stats */}
      <div className="grid grid-cols-4 gap-2 border-b px-4 py-3">
        {(Object.entries(layerStats) as [string, number][]).map(([layer, count]) => (
          <button
            key={layer}
            onClick={() => setSelectedType(selectedType === layer ? null : layer)}
            className={`rounded-md border px-2 py-1.5 text-center text-xs transition-colors ${
              selectedType === layer
                ? "border-blue-500 bg-blue-50"
                : "border-gray-200 hover:border-gray-300"
            }`}
          >
            <div className="font-medium">{LAYER_LABELS[layer]}</div>
            <div className="text-lg font-bold">{count}</div>
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 border-b px-4 py-2">
        <Filter className="h-3.5 w-3.5 text-gray-400" />
        <select
          className="rounded border px-2 py-1 text-xs"
          value={selectedAgentId ?? ""}
          onChange={(e) => setSelectedAgentId(e.target.value || null)}
        >
          <option value="">All Agents</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </div>

      {/* Memory list */}
      <div className="flex-1 overflow-y-auto p-4">
        {filteredItems.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            暂无记忆数据
          </div>
        ) : (
          <div className="space-y-2">
            {filteredItems.map((item) => (
              <MemoryCard key={item.id} item={item} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryCard({ item }: { item: ReturnType<typeof useMemoryStore.getState>["items"][0] }) {
  return (
    <div className="rounded-lg border bg-white p-3 text-xs shadow-sm">
      <div className="flex items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${LAYER_COLORS[item.memoryType] ?? ""}`}>
          {LAYER_LABELS[item.memoryType] ?? item.memoryType}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${SCOPE_COLORS[item.scope] ?? ""}`}>
          {item.scope}
        </span>
        <span className="ml-auto flex items-center gap-1 text-gray-400">
          <Star className="h-3 w-3" />
          {item.importance.toFixed(2)}
        </span>
      </div>
      <p className="mt-2 text-gray-700 line-clamp-3">{item.content}</p>
      <div className="mt-2 flex items-center gap-2 text-[10px] text-gray-400">
        <Clock className="h-3 w-3" />
        {item.createdAt ? new Date(item.createdAt).toLocaleString() : "N/A"}
      </div>
    </div>
  );
}
