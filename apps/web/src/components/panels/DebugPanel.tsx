"use client";

import { useDebugStore, type ExecutionEvent, type MemoryEvent } from "@/stores/debugStore";
import {
  Bug,
  Play,
  Pause,
  SkipForward,
  SkipBack,
  RotateCcw,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  Zap,
  Brain,
  ArrowRightLeft,
} from "lucide-react";

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />,
  done: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />,
  error: <XCircle className="h-3.5 w-3.5 text-red-500" />,
};

const MEMORY_EVENT_ICON: Record<string, React.ReactNode> = {
  compress: <Zap className="h-3 w-3 text-amber-500" />,
  forget: <Brain className="h-3 w-3 text-purple-500" />,
  migrate: <ArrowRightLeft className="h-3 w-3 text-cyan-500" />,
};

function MemoryEventCard({ evt }: { evt: MemoryEvent }) {
  const label = evt.event;
  const icon = MEMORY_EVENT_ICON[label] ?? <Zap className="h-3 w-3" />;
  const agent = evt.agentId ? evt.agentId.slice(0, 8) : "—";

  let detail = "";
  if (label === "compress") {
    detail = `${evt.originalCount ?? 0}→${evt.retainedCount ?? 0} (${evt.summaryCount ?? 0} summaries, ${evt.level ?? "?"})`;
  } else if (label === "forget") {
    detail = `scanned ${evt.scanned ?? 0}, archived ${evt.archived ?? 0}`;
  } else if (label === "migrate") {
    detail = `${evt.path ?? "?"}: ${evt.count ?? 0} items`;
  }

  return (
    <div className="flex items-center gap-2 rounded bg-gray-50 px-3 py-1.5 text-xs">
      {icon}
      <span className="font-medium capitalize">{label}</span>
      <span className="text-gray-500">agent:{agent}</span>
      <span className="ml-auto text-gray-400">{detail}</span>
    </div>
  );
}

export function DebugPanel() {
  const debugMode = useDebugStore((s) => s.debugMode);
  const toggleDebugMode = useDebugStore((s) => s.toggleDebugMode);
  const events = useDebugStore((s) => s.executionEvents);
  const memoryEvents = useDebugStore((s) => s.memoryEvents);
  const replayIndex = useDebugStore((s) => s.replayIndex);
  const setReplayIndex = useDebugStore((s) => s.setReplayIndex);
  const clearHistory = useDebugStore((s) => s.clearHistory);

  const currentEvent = replayIndex >= 0 ? events[replayIndex] : null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Bug className="h-4 w-4 text-red-500" />
          调试面板
        </div>
        <button
          onClick={toggleDebugMode}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            debugMode
              ? "bg-red-100 text-red-700"
              : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          {debugMode ? "调试中" : "开启调试"}
        </button>
      </div>

      {/* Replay controls */}
      {events.length > 0 && (
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <button
            onClick={() => setReplayIndex(-1)}
            className="rounded p-1 hover:bg-gray-100"
            title="停止回放"
          >
            <Pause className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setReplayIndex(Math.max(0, (replayIndex <= 0 ? events.length : replayIndex) - 1))}
            className="rounded p-1 hover:bg-gray-100"
            title="上一步"
          >
            <SkipBack className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setReplayIndex(Math.min(events.length - 1, (replayIndex < 0 ? -1 : replayIndex) + 1))}
            className="rounded p-1 hover:bg-gray-100"
            title="下一步"
          >
            <SkipForward className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={clearHistory}
            className="rounded p-1 hover:bg-gray-100"
            title="清除历史"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <span className="ml-2 text-xs text-gray-400">
            {replayIndex >= 0 ? `${replayIndex + 1} / ${events.length}` : `${events.length} 步`}
          </span>
        </div>
      )}

      {/* Current event detail */}
      {currentEvent && (
        <div className="border-b bg-yellow-50 p-4 text-xs">
          <div className="flex items-center gap-2 font-semibold">
            {STATUS_ICON[currentEvent.status]}
            Node: {currentEvent.nodeId}
            <span className="ml-auto text-gray-400">
              Agent: {currentEvent.agentId.slice(0, 8)}
            </span>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div>
              <div className="font-medium text-gray-600">Input</div>
              <pre className="mt-1 max-h-20 overflow-auto rounded bg-white p-2 text-[10px]">
                {currentEvent.input || "(empty)"}
              </pre>
            </div>
            <div>
              <div className="font-medium text-gray-600">Output</div>
              <pre className="mt-1 max-h-20 overflow-auto rounded bg-white p-2 text-[10px]">
                {currentEvent.output || "(empty)"}
              </pre>
            </div>
          </div>
          <div className="mt-2 flex gap-3 text-gray-400">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {currentEvent.executionTimeMs}ms
            </span>
            <span>{new Date(currentEvent.timestamp).toLocaleTimeString()}</span>
          </div>
        </div>
      )}

      {/* Memory lifecycle events */}
      {memoryEvents.length > 0 && (
        <div className="border-b">
          <div className="px-4 py-2 text-xs font-semibold text-gray-500">
            记忆事件 ({memoryEvents.length})
          </div>
          <div className="max-h-40 space-y-1 overflow-y-auto px-4 pb-3">
            {[...memoryEvents].reverse().map((evt, idx) => (
              <MemoryEventCard key={idx} evt={evt} />
            ))}
          </div>
        </div>
      )}

      {/* Event timeline */}
      <div className="flex-1 overflow-y-auto p-4">
        {events.length === 0 && memoryEvents.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            {debugMode ? "等待执行事件..." : "开启调试模式以记录事件"}
          </div>
        ) : (
          <div className="space-y-1">
            {events.map((event, idx) => (
              <button
                key={event.id}
                onClick={() => setReplayIndex(idx)}
                className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-xs transition-colors ${
                  idx === replayIndex
                    ? "bg-blue-50 border border-blue-200"
                    : "hover:bg-gray-50"
                }`}
              >
                {STATUS_ICON[event.status]}
                <span className="font-medium">{event.nodeId}</span>
                <span className="ml-auto text-gray-400">
                  {event.executionTimeMs}ms
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
