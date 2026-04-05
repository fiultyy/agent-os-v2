"use client";

import { useState, useEffect } from "react";
import { useMemoryStore, type MemoryItem } from "@/stores/memoryStore";
import { useAgentStore } from "@/stores/agentStore";
import { getMemories, getAgents } from "@/lib/api";
import { Brain, Search, Star, Clock, Filter, ChevronDown, ChevronRight } from "lucide-react";

const LAYER_TABS = [
  { key: "", label: "All" },
  { key: "working", label: "L0 Working" },
  { key: "session", label: "L1 Session" },
  { key: "episodic", label: "L2 Episodic" },
  { key: "semantic", label: "L3 Semantic" },
] as const;

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

const IMPORTANCE_COLORS = [
  { max: 0.3, color: "bg-gray-300" },
  { max: 0.6, color: "bg-yellow-400" },
  { max: 0.8, color: "bg-blue-500" },
  { max: 1.01, color: "bg-green-500" },
];

function importanceColor(value: number) {
  return IMPORTANCE_COLORS.find((c) => value < c.max)?.color ?? "bg-gray-300";
}

function formatTime(iso: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function MemoryPanel() {
  const items = useMemoryStore((s) => s.items);
  const setItems = useMemoryStore((s) => s.setItems);
  const setLoading = useMemoryStore((s) => s.setLoading);
  const loading = useMemoryStore((s) => s.loading);
  const agents = useAgentStore((s) => s.agents);

  const [activeLayer, setActiveLayer] = useState("");
  const [search, setSearch] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"time" | "importance">("time");

  // Load memories on mount and when filters change
  useEffect(() => {
    setLoading(true);
    getMemories(agentFilter || undefined, activeLayer || undefined, undefined, 200)
      .then((data) => setItems(data))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [agentFilter, activeLayer, setItems, setLoading]);

  // Filter and sort
  const filtered = items
    .filter((m) => {
      if (search && !m.content.toLowerCase().includes(search.toLowerCase())) return false;
      if (activeLayer && m.memoryType !== activeLayer) return false;
      if (agentFilter && m.agentId !== agentFilter) return false;
      return !m.archived;
    })
    .sort((a, b) => {
      if (sortBy === "importance") return b.importance - a.importance;
      return (b.createdAt > a.createdAt ? 1 : -1);
    });

  // Layer stats
  const stats = { working: 0, session: 0, episodic: 0, semantic: 0 };
  items.forEach((m) => {
    if (!m.archived && m.memoryType in stats) {
      stats[m.memoryType as keyof typeof stats]++;
    }
  });

  return (
    <div className="flex h-full flex-col">
      {/* Header with search and filters */}
      <div className="border-b bg-white p-4">
        <div className="flex items-center gap-2 mb-3">
          <Brain className="h-5 w-5 text-blue-600" />
          <h2 className="text-lg font-semibold">Memory</h2>
          <span className="text-sm text-gray-400">({filtered.length} items)</span>
        </div>

        {/* Search bar */}
        <div className="relative mb-3">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search memories..."
            className="w-full rounded-lg border border-gray-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-blue-400"
          />
        </div>

        {/* Layer tabs */}
        <div className="flex gap-1 overflow-x-auto">
          {LAYER_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveLayer(tab.key)}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                activeLayer === tab.key
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {tab.label}
              {tab.key && stats[tab.key as keyof typeof stats] !== undefined && (
                <span className="ml-1 opacity-70">({stats[tab.key as keyof typeof stats]})</span>
              )}
            </button>
          ))}
        </div>

        {/* Filters row */}
        <div className="mt-3 flex items-center gap-2">
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-lg border border-gray-200 px-2 py-1 text-xs"
          >
            <option value="">All Agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <button
            onClick={() => setSortBy(sortBy === "time" ? "importance" : "time")}
            className="flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
          >
            <Filter className="h-3 w-3" />
            Sort: {sortBy === "time" ? "Time" : "Importance"}
          </button>
        </div>
      </div>

      {/* Memory list */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center py-12 text-gray-400">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
            <span className="ml-2 text-sm">Loading memories...</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <Brain className="mb-2 h-8 w-8" />
            <p className="text-sm">No memories found</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((item) => (
              <div
                key={item.id}
                onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
                className="cursor-pointer rounded-lg border bg-white p-3 transition-shadow hover:shadow-sm"
              >
                {/* Header row */}
                <div className="flex items-start gap-2">
                  <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${LAYER_COLORS[item.memoryType] || "bg-gray-100"}`}>
                    {item.memoryType}
                  </span>
                  <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${SCOPE_COLORS[item.scope] || "bg-gray-100"}`}>
                    {item.scope}
                  </span>
                  <p className="min-w-0 flex-1 text-sm text-gray-700 line-clamp-2">
                    {expandedId === item.id ? item.content : item.content.slice(0, 120) + (item.content.length > 120 ? "..." : "")}
                  </p>
                  {expandedId === item.id ? (
                    <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" />
                  )}
                </div>

                {/* Metadata row */}
                <div className="mt-2 flex items-center gap-3 text-xs text-gray-400">
                  <span className="flex items-center gap-1">
                    <Star className="h-3 w-3" />
                    <span className={`inline-block h-1.5 w-8 rounded-full ${importanceColor(item.importance)}`} />
                    <span>{item.importance.toFixed(2)}</span>
                  </span>
                  <span className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {formatTime(item.createdAt)}
                  </span>
                  <span className="text-[10px] text-gray-400">
                    {item.agentId.slice(0, 8)}
                  </span>
                </div>

                {/* Expanded content */}
                {expandedId === item.id && (
                  <div className="mt-3 border-t pt-2">
                    <pre className="whitespace-pre-wrap text-xs text-gray-600">{item.content}</pre>
                    {item.metadata && Object.keys(item.metadata).length > 0 && (
                      <div className="mt-2 rounded bg-gray-50 p-2 text-[10px] text-gray-500">
                        {JSON.stringify(item.metadata, null, 2).slice(0, 300)}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
