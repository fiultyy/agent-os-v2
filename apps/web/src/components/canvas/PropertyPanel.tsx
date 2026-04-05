"use client";

import { useState, useEffect } from "react";
import { useFlowStore } from "@/stores/flowStore";
import { useAgentStore } from "@/stores/agentStore";
import { createAgent, getMemories } from "@/lib/api";
import { type MemoryItem } from "@/stores/memoryStore";
import { X, Save, Loader2 } from "lucide-react";

const IMPORTANCE_COLORS = [
  { max: 0.3, color: "bg-gray-300" },
  { max: 0.6, color: "bg-yellow-400" },
  { max: 0.8, color: "bg-blue-500" },
  { max: 1.01, color: "bg-green-500" },
];

function importanceColor(value: number) {
  return IMPORTANCE_COLORS.find((c) => value < c.max)?.color ?? "bg-gray-300";
}

const MODEL_OPTIONS = [
  { value: "glm-4-flash", label: "GLM-4-Flash" },
  { value: "glm-4-plus", label: "GLM-4-Plus" },
  { value: "glm-4-long", label: "GLM-4-Long" },
  { value: "gpt-4o-mini", label: "GPT-4o-mini" },
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
];

export function PropertyPanel() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const setSelectedNodeId = useFlowStore((s) => s.setSelectedNodeId);
  const agents = useAgentStore((s) => s.agents);
  const addAgent = useAgentStore((s) => s.addAgent);
  const updateAgent = useAgentStore((s) => s.updateAgent);

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [recentMemories, setRecentMemories] = useState<MemoryItem[]>([]);

  // Load recent memories when an agent node is selected
  useEffect(() => {
    if (selectedNodeId) {
      const node = nodes.find((n) => n.id === selectedNodeId);
      if (node?.type === "agent" && node.data.agentId) {
        getMemories(node.data.agentId as string, undefined, undefined, 5)
          .then(setRecentMemories)
          .catch(() => setRecentMemories([]));
      } else {
        setRecentMemories([]);
      }
    }
  }, [selectedNodeId, nodes]);

  const node = nodes.find((n) => n.id === selectedNodeId);
  if (!node) return null;

  const data = node.data as Record<string, unknown>;
  const label = String(data.label ?? "");
  const description = String(data.description ?? "");
  const isAgent = node.type === "agent";

  // Agent-specific fields
  const model = String(data.model ?? "glm-4-flash");
  const systemPrompt = String(data.systemPrompt ?? "");
  const agentId = data.agentId as string | undefined;
  const memoryCount = Number(data.memoryCount ?? 0);

  async function handleSave() {
    if (!isAgent) return;
    // Re-fetch node to avoid TypeScript closure issue
    const currentNode = nodes.find((n) => n.id === selectedNodeId);
    if (!currentNode) return;
    setSaving(true);
    setSaveMsg(null);

    try {
      if (agentId) {
        // Update existing agent
        updateAgent(agentId, {
          name: label,
          description,
          model,
        });
        setSaveMsg("已保存");
      } else {
        // Create new agent via API
        const remote = await createAgent({
          name: label,
          description: description || undefined,
          model,
        });
        updateNodeData(currentNode.id, { agentId: remote.id, label: remote.name });
        addAgent({
          id: remote.id,
          name: remote.name,
          description: remote.description ?? "",
          status: remote.status ?? "idle",
          model: remote.model ?? model,
          tools: remote.tools ?? [],
          createdAt: remote.createdAt ?? new Date().toISOString(),
          updatedAt: remote.updatedAt ?? new Date().toISOString(),
        });
        setSaveMsg("已创建并保存");
      }
    } catch (err) {
      setSaveMsg(`保存失败: ${err}`);
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 3000);
    }
  }

  return (
    <div className="flex h-full w-80 flex-col border-l bg-white">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <span className="text-sm font-semibold">
          {isAgent ? "Agent 配置" : node.type === "tool" ? "Tool 属性" : "Prompt 属性"}
        </span>
        <button
          onClick={() => setSelectedNodeId(null)}
          className="text-gray-400 hover:text-gray-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {/* Common fields */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">名称</label>
          <input
            className="w-full rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            value={label}
            onChange={(e) => updateNodeData(node.id, { label: e.target.value })}
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500">描述</label>
          <textarea
            className="w-full rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            rows={2}
            value={description}
            placeholder="可选描述..."
            onChange={(e) => updateNodeData(node.id, { description: e.target.value })}
          />
        </div>

        {/* Agent-specific config */}
        {isAgent && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">模型</label>
              <select
                className="w-full rounded border px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
                value={model}
                onChange={(e) => updateNodeData(node.id, { model: e.target.value })}
              >
                {MODEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">System Prompt</label>
              <textarea
                className="w-full rounded border px-2 py-1.5 text-sm font-mono focus:border-blue-500 focus:outline-none"
                rows={6}
                value={systemPrompt}
                placeholder="你是一个智能助手..."
                onChange={(e) => updateNodeData(node.id, { systemPrompt: e.target.value })}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">状态</label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.status ?? "idle")}
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">Memory 条目</label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {memoryCount}
              </div>
            </div>

            {/* Recent Memories */}
            <div className="border-t pt-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">Recent Memories</h3>
              {recentMemories.length === 0 ? (
                <p className="text-xs text-gray-400">No memories yet</p>
              ) : (
                <div className="space-y-2">
                  {recentMemories.slice(0, 3).map((m) => (
                    <div key={m.id} className="rounded border bg-gray-50 p-2">
                      <p className="text-xs text-gray-600 line-clamp-2">{m.content.slice(0, 80)}</p>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-gray-400">
                        <span className={`inline-block h-1 w-6 rounded-full ${importanceColor(m.importance)}`} />
                        <span>{m.importance.toFixed(2)}</span>
                        <span>{m.memoryType}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Save button */}
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex w-full items-center justify-center gap-1.5 rounded bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {agentId ? "保存配置" : "创建 Agent"}
            </button>

            {saveMsg && (
              <div className={`text-xs ${saveMsg.includes("失败") ? "text-red-500" : "text-green-600"}`}>
                {saveMsg}
              </div>
            )}
          </>
        )}

        {/* Tool-specific */}
        {node.type === "tool" && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">最近状态</label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.lastStatus ?? "idle")}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">耗时 (ms)</label>
              <div className="rounded bg-gray-50 px-2 py-1.5 text-xs">
                {String(data.duration ?? 0)}
              </div>
            </div>
          </>
        )}

        {/* Prompt-specific */}
        {node.type === "prompt" && (
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-500">模板变量（逗号分隔）</label>
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
      </div>
    </div>
  );
}
