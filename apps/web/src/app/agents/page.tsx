"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Header } from "@/components/layout/Header";
import { getAgents, createAgent, deleteAgent } from "@/lib/api";
import type { AgentItem } from "@/stores/agentStore";
import { Bot, Plus, Trash2, ArrowRight, Loader2, ChevronDown, X } from "lucide-react";

const statusColor: Record<string, string> = {
  idle: "bg-gray-300",
  running: "bg-green-500 animate-pulse",
  error: "bg-red-500",
  stopped: "bg-yellow-500",
};

const MODEL_OPTIONS = ["glm-4.7", "glm-4-flash", "glm-5-turbo"];

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 创建表单(展开式):name / model / system_prompt。
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formModel, setFormModel] = useState(MODEL_OPTIONS[0]);
  const [formPrompt, setFormPrompt] = useState("");

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

  function resetForm() {
    setFormName("");
    setFormModel(MODEL_OPTIONS[0]);
    setFormPrompt("");
  }

  async function handleCreate() {
    // 表单提交:WIRE create-agent intent(name/model/system_prompt 三入参)。
    const name = formName.trim() || `Agent ${agents.length + 1}`;
    setCreating(true);
    try {
      const agent = await createAgent({
        name,
        model: formModel,
        system_prompt: formPrompt,
      });
      setAgents((prev) => [...prev, agent]);
      resetForm();
      setShowForm(false);
      // 刷新列表(与后端最新状态对齐,而非仅本地 append)。
      await loadAgents();
    } catch (err) {
      setError(`创建失败: ${err}`);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    // 去 window.confirm:headless/自动化浏览器 confirm() 自动 dismiss(false)阻断删除;
    // 单用户系统误删风险低,直接删。如需保护改自定义 modal(非原生 confirm)。
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
              onClick={() => {
                setShowForm((v) => !v);
                if (showForm) resetForm();
              }}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
              aria-label="新建 Agent"
            >
              <Plus className="h-4 w-4" />
              新建 Agent
              <ChevronDown
                className={`h-4 w-4 transition-transform ${showForm ? "rotate-180" : ""}`}
              />
            </button>
          </div>

          {/* 创建表单(展开式)— WIRE create-agent intent */}
          {showForm && (
            <div className="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-base font-semibold text-gray-900">创建 Agent</h2>
                <button
                  onClick={() => {
                    setShowForm(false);
                    resetForm();
                  }}
                  className="text-gray-400 hover:text-gray-600"
                  aria-label="关闭创建表单"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="agent-name"
                      className="mb-1 block text-sm font-medium text-gray-700"
                    >
                      名称
                    </label>
                    <input
                      id="agent-name"
                      type="text"
                      value={formName}
                      onChange={(e) => setFormName(e.target.value)}
                      placeholder={`Agent ${agents.length + 1}`}
                      aria-label="agent 名称"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="agent-model"
                      className="mb-1 block text-sm font-medium text-gray-700"
                    >
                      模型
                    </label>
                    <select
                      id="agent-model"
                      value={formModel}
                      onChange={(e) => setFormModel(e.target.value)}
                      aria-label="agent 模型"
                      className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    >
                      {MODEL_OPTIONS.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="agent-prompt"
                    className="mb-1 block text-sm font-medium text-gray-700"
                  >
                    系统提示词
                  </label>
                  <textarea
                    id="agent-prompt"
                    value={formPrompt}
                    onChange={(e) => setFormPrompt(e.target.value)}
                    placeholder="定义此 Agent 的角色、能力与行为约束…"
                    aria-label="agent 系统提示词"
                    rows={4}
                    className="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                <div className="flex items-center justify-end gap-2">
                  <button
                    onClick={() => {
                      setShowForm(false);
                      resetForm();
                    }}
                    disabled={creating}
                    className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleCreate}
                    disabled={creating}
                    aria-label="创建 agent"
                    className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {creating ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plus className="h-4 w-4" />
                    )}
                    创建 agent
                  </button>
                </div>
              </div>
            </div>
          )}

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
                      aria-label="删除 agent"
                      className="text-gray-400 opacity-60 transition-opacity hover:opacity-100 hover:text-red-500"
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
