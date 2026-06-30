"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Workflow,
  Loader2,
  Plus,
  Trash2,
  Play,
  CheckCircle2,
  XCircle,
  GitBranch,
  GitMerge,
  Sparkles,
} from "lucide-react";
import { orchestrateWithSSE, type OrchestrateSubAgent, type SSEEvent } from "@/lib/api";
import { dispatchSSEEvent } from "@/lib/sse-dispatch";
import { useDebugStore } from "@/stores/debugStore";

// ── Types ─────────────────────────────────────────────────────

interface Agent {
  id: string;
  name: string;
  description: string;
  model: string;
}

interface BranchResult {
  branch?: string;
  role?: string;
  output?: string;
  status?: string;
}

interface NodeLog {
  node: string;
  status: "running" | "done" | "error";
  output?: string;
  branches?: BranchResult[];
}

// ── Component ─────────────────────────────────────────────────

export function OrchestrationPanel() {
  // orchestrator agent 列表(复用 page.tsx 的 fetch /api/agents 模式)
  const [agents, setAgents] = useState<Agent[]>([]);
  const [orchestratorId, setOrchestratorId] = useState<string>("");
  const [input, setInput] = useState("");
  const [subAgents, setSubAgents] = useState<OrchestrateSubAgent[]>([
    { role: "researcher", system_prompt: "", input: "" },
    { role: "critic", system_prompt: "", input: "" },
  ]);

  // SSE 实时状态
  const [loading, setLoading] = useState(false);
  const [nodeLogs, setNodeLogs] = useState<NodeLog[]>([]);
  const [finalOutput, setFinalOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Forward store-bound SSE events (memory / agent-message / runtime-observation)
  // to the debug store, alongside the local node-log handling below.
  const addMemoryEvent = useDebugStore((s) => s.addMemoryEvent);
  const addMessage = useDebugStore((s) => s.addMessage);
  const addObservationEvent = useDebugStore((s) => s.addObservationEvent);

  useEffect(() => {
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : []))
      .then((list: Agent[]) => {
        setAgents(list);
        if (list.length > 0 && !orchestratorId) setOrchestratorId(list[0].id);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateSubAgent = (idx: number, patch: Partial<OrchestrateSubAgent>) => {
    setSubAgents((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, ...patch } : s))
    );
  };

  const addSubAgent = () => {
    setSubAgents((prev) => [
      ...prev,
      { role: `role_${prev.length + 1}`, system_prompt: "", input: "" },
    ]);
  };

  const removeSubAgent = (idx: number) => {
    setSubAgents((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx)));
  };

  const run = useCallback(async () => {
    if (!orchestratorId || !input.trim() || loading) return;
    setLoading(true);
    setError(null);
    setFinalOutput(null);
    setNodeLogs([]);

    try {
      await orchestrateWithSSE(
        orchestratorId,
        input,
        subAgents,
        undefined,
        (event: SSEEvent) => {
          dispatchSSEEvent(event, { addMemoryEvent, addMessage, addObservationEvent });
          handleSSEEvent(event, setNodeLogs, setFinalOutput, setError);
        }
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [orchestratorId, input, subAgents, loading]);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Workflow className="h-4 w-4 text-purple-600" />
          多 Agent 编排
        </div>
        <p className="mt-1 text-xs text-gray-500">
          Fan-out N 角色 subagent → fan-in → orchestrator 综合(SSE)
        </p>
      </div>

      {/* Config */}
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {/* Orchestrator agent 选择(radio 按钮组:stagehand click 友好 + a11y,替代 select 下拉) */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">
            Orchestrator Agent
          </label>
          <div className="space-y-1.5">
            {agents.length === 0 && <div className="text-xs text-gray-400">(无可用 agent)</div>}
            {agents.map((a) => (
              <button
                key={a.id}
                type="button"
                role="radio"
                aria-checked={orchestratorId === a.id}
                aria-label={`选择 orchestrator agent ${a.name}`}
                onClick={() => setOrchestratorId(a.id)}
                className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${orchestratorId === a.id ? "border-purple-500 bg-purple-50" : "border-gray-200 hover:border-purple-300"}`}
              >
                <span className={`flex h-4 w-4 items-center justify-center rounded-full border-2 ${orchestratorId === a.id ? "border-purple-500" : "border-gray-300"}`}>
                  {orchestratorId === a.id && <span className="h-2 w-2 rounded-full bg-purple-500" />}
                </span>
                <span className="flex-1">
                  <span className="block font-medium text-gray-800">{a.name}</span>
                  <span className="block text-xs text-gray-500">{a.model}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 编排输入 */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">
            编排输入
          </label>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入编排任务,由 orchestrator 综合各 subagent 结果..."
            aria-label="编排任务输入"
            rows={3}
            className="w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-purple-400"
          />
        </div>

        {/* Sub-agents 配置 */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <label className="text-xs font-medium text-gray-600">
              Sub-agents({subAgents.length})
            </label>
            <button
              onClick={addSubAgent}
              className="flex items-center gap-1 rounded-md bg-purple-50 px-2 py-1 text-xs text-purple-700 hover:bg-purple-100"
            >
              <Plus className="h-3 w-3" />
              添加角色
            </button>
          </div>
          <div className="space-y-2">
            {subAgents.map((sa, idx) => (
              <div key={idx} className="rounded-lg border bg-white p-2.5 shadow-sm">
                <div className="flex items-center gap-2">
                  <input
                    value={sa.role}
                    onChange={(e) => updateSubAgent(idx, { role: e.target.value })}
                    placeholder="role(e.g. researcher)"
                    className="flex-1 rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-purple-400"
                  />
                  <button
                    onClick={() => removeSubAgent(idx)}
                    disabled={subAgents.length <= 1}
                    title="删除角色"
                    className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-red-500 disabled:opacity-30"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <textarea
                  value={sa.system_prompt ?? ""}
                  onChange={(e) => updateSubAgent(idx, { system_prompt: e.target.value })}
                  placeholder="system_prompt(可选)"
                  aria-label="sub-agent system_prompt 输入"
                  rows={2}
                  className="mt-1.5 w-full resize-none rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-purple-400"
                />
              </div>
            ))}
          </div>
        </div>

        {/* 触发按钮 */}
        <button
          onClick={run}
          disabled={loading || !orchestratorId || !input.trim()}
          aria-label="触发编排"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-purple-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-purple-700 disabled:opacity-40"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {loading ? "编排中..." : "触发编排"}
        </button>

        {/* 错误 */}
        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            <XCircle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {/* SSE 节点日志 */}
        {nodeLogs.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-gray-500">编排流</div>
            {nodeLogs.map((log, i) => (
              <NodeLogCard key={i} log={log} />
            ))}
          </div>
        )}

        {/* 最终综合输出 */}
        {finalOutput && (
          <div className="rounded-lg border border-purple-200 bg-purple-50 p-3">
            <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold text-purple-700">
              <Sparkles className="h-4 w-4" />
              综合输出(orchestrator)
            </div>
            <pre className="whitespace-pre-wrap break-words rounded bg-white p-2 text-xs text-gray-800">
              {finalOutput}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

// ── SSE event handler ─────────────────────────────────────────
//
// 解析 /v1/orchestrate 的节点事件(见后端 orchestrate.py):multi_agent /
// fan_in / synthesizer / execution_complete。错误事件映射到 error 状态。

function handleSSEEvent(
  event: SSEEvent,
  setNodeLogs: React.Dispatch<React.SetStateAction<NodeLog[]>>,
  setFinalOutput: (s: string) => void,
  setError: (s: string) => void
): void {
  if (event.event === "node_start") {
    const node = (event.data.node as string) ?? "unknown";
    setNodeLogs((prev) => [...prev, { node, status: "running" }]);
  } else if (event.event === "node_complete") {
    const node = (event.data.node as string) ?? "unknown";
    const status = (event.data.status as NodeLog["status"]) ?? "done";
    const branches = (event.data.branches as BranchResult[]) ?? undefined;
    const output = (event.data.output as string) ?? undefined;
    setNodeLogs((prev) => {
      // 找到最后一个同名 running 节点,更新为 done;否则追加。
      const idx = [...prev].reverse().findIndex((l) => l.node === node && l.status === "running");
      if (idx === -1) return [...prev, { node, status, output, branches }];
      const realIdx = prev.length - 1 - idx;
      const next = [...prev];
      next[realIdx] = { ...next[realIdx], status, output, branches };
      return next;
    });
  } else if (event.event === "execution_complete") {
    const out = (event.data.output as string) ?? "";
    if (out) setFinalOutput(out);
  } else if (event.event === "error") {
    setError((event.data.message as string) ?? "Unknown orchestration error");
  }
}

// ── Node log card ─────────────────────────────────────────────

const NODE_ICON: Record<string, React.ReactNode> = {
  multi_agent: <GitBranch className="h-3.5 w-3.5 text-blue-500" />,
  fan_in: <GitMerge className="h-3.5 w-3.5 text-amber-500" />,
  synthesizer: <Sparkles className="h-3.5 w-3.5 text-purple-500" />,
};

function NodeLogCard({ log }: { log: NodeLog }) {
  const icon = NODE_ICON[log.node] ?? <Workflow className="h-3.5 w-3.5 text-gray-500" />;
  const statusIcon =
    log.status === "running" ? (
      <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
    ) : log.status === "error" ? (
      <XCircle className="h-3 w-3 text-red-500" />
    ) : (
      <CheckCircle2 className="h-3 w-3 text-green-500" />
    );

  return (
    <div className="rounded-lg border bg-white p-3 text-xs shadow-sm">
      <div className="flex items-center gap-2">
        {icon}
        <span className="font-medium">{log.node}</span>
        <span className="ml-auto">{statusIcon}</span>
      </div>

      {/* multi_agent 分支展开 */}
      {log.branches && log.branches.length > 0 && (
        <div className="mt-2 space-y-1">
          {log.branches.map((b, i) => (
            <div key={i} className="rounded bg-gray-50 px-2 py-1">
              <div className="flex items-center gap-1">
                <span className="font-mono text-[10px] text-blue-600">
                  {b.role ?? b.branch ?? `branch_${i}`}
                </span>
                {b.status && (
                  <span className="ml-auto text-[10px] text-gray-400">{b.status}</span>
                )}
              </div>
              {b.output && (
                <pre className="mt-0.5 whitespace-pre-wrap break-words text-[10px] text-gray-600">
                  {b.output.length > 200 ? b.output.slice(0, 200) + "..." : b.output}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}

      {/* fan_in / synthesizer 输出 */}
      {log.output && log.node !== "multi_agent" && (
        <pre className="mt-1.5 whitespace-pre-wrap break-words text-[10px] text-gray-600">
          {log.output.length > 300 ? log.output.slice(0, 300) + "..." : log.output}
        </pre>
      )}
    </div>
  );
}
