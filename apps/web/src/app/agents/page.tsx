"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Header } from "@/components/layout/Header";
import { getAgents, createAgent, deleteAgent } from "@/lib/api";
import type { AgentItem } from "@/stores/agentStore";
import { Bot, Plus, Trash2, ArrowRight, Loader2 } from "lucide-react";

const statusColor: Record<string, string> = {
  idle: "bg-gray-300",
  running: "bg-green-500 animate-pulse",
  error: "bg-red-500",
  stopped: "bg-yellow-500",
};

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadAgents();
  }, []);

  async function loadAgents() {
    setLoading(true);
    setError(null);
    try {
      const list = await getAgents();
      setAgents(list);
    } catch (err) {
      setError(`加载失败: ${err}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    setCreating(true);
    try {
      const agent = await createAgent({
        name: `Agent ${agents.length + 1}`,
        model: "glm-4-flash",
      });
      setAgents((prev) => [...prev, agent]);
    } catch (err) {
      setError(`创建失败: ${err}`);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("确定删除此 Agent？")) return;
    try {
      await deleteAgent(id);
      setAgents((prev) => prev.filter((a) => a.id !== id));
    } catch (err) {
      setError(`删除失败: ${err}`);
    }
  }

  function formatDate(iso: string) {
    if (!iso) return "-";
    try {
      return new Date(iso).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  return (
    <div className="flex h-screen flex-col">
      <Header />
      <div className="flex-1 overflow-auto bg-gray-50 p-6">
        <div className="mx-auto max-w-5xl">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Agent 管理</h1>
              <p className="mt-1 text-sm text-gray-500">共 {agents.length} 个 Agent</p>
            </div>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              新建 Agent
            </button>
          </div>

          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600">
              {error}
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-20 text-gray-400">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              加载中...
            </div>
          )}

          {/* Empty state */}
          {!loading && agents.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-gray-400">
              <Bot className="mb-3 h-12 w-12" />
              <p className="text-lg font-medium">暂无 Agent</p>
              <p className="mt-1 text-sm">点击「新建 Agent」创建你的第一个智能体</p>
            </div>
          )}

          {/* Agent list */}
          {!loading && agents.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {agents.map((agent) => (
                <Link
                  key={agent.id}
                  href="/canvas"
                  className="group rounded-lg border bg-white p-4 shadow-sm transition-shadow hover:shadow-md"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <Bot className="h-5 w-5 text-blue-600" />
                      <h3 className="font-semibold text-gray-900">{agent.name}</h3>
                    </div>
                    <button
                      onClick={(e) => handleDelete(agent.id, e)}
                      className="text-gray-300 opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                      title="删除"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  {agent.description && (
                    <p className="mt-2 text-sm text-gray-500 line-clamp-2">{agent.description}</p>
                  )}

                  <div className="mt-3 flex items-center gap-3 text-xs text-gray-400">
                    <span className="flex items-center gap-1">
                      <span className={`inline-block h-2 w-2 rounded-full ${statusColor[agent.status] ?? statusColor.idle}`} />
                      {agent.status}
                    </span>
                    <span>{agent.model}</span>
                    <span>{formatDate(agent.createdAt)}</span>
                  </div>

                  <div className="mt-3 flex items-center text-xs text-blue-500 opacity-0 transition-opacity group-hover:opacity-100">
                    打开画布
                    <ArrowRight className="ml-1 h-3 w-3" />
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
