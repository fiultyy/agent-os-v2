"use client";

import { useDebugStore, type ExecutionEvent } from "@/stores/debugStore";
import { History, Play, Clock } from "lucide-react";

// ── Helpers ────────────────────────────────────────────────────

interface RunSummary {
  startTime: string;
  totalTimeMs: number;
  events: ExecutionEvent[];
  errors: number;
}

function groupIntoRuns(events: ExecutionEvent[]): RunSummary[] {
  if (events.length === 0) return [];

  const runs: RunSummary[] = [];
  let currentRun: RunSummary = {
    startTime: events[0].timestamp,
    totalTimeMs: 0,
    events: [],
    errors: 0,
  };

  const GAP_MS = 60_000; // 60s gap = new run

  for (const event of events) {
    const eventTime = new Date(event.timestamp).getTime();
    const lastTime = currentRun.events.length > 0
      ? new Date(currentRun.events[currentRun.events.length - 1].timestamp).getTime()
      : eventTime;

    if (currentRun.events.length > 0 && eventTime - lastTime > GAP_MS) {
      runs.push(currentRun);
      currentRun = {
        startTime: event.timestamp,
        totalTimeMs: 0,
        events: [],
        errors: 0,
      };
    }

    currentRun.events.push(event);
    currentRun.totalTimeMs += event.executionTimeMs;
    if (event.status === "error") currentRun.errors++;
  }

  runs.push(currentRun);
  return runs;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── Component ──────────────────────────────────────────────────

export function ExecutionHistoryPanel() {
  const events = useDebugStore((s) => s.executionEvents);
  const setReplayIndex = useDebugStore((s) => s.setReplayIndex);

  const runs = groupIntoRuns(events);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <History className="h-4 w-4 text-indigo-600" />
          执行历史
        </div>
        <p className="mt-1 text-xs text-gray-500">
          查看和回放历史执行
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {runs.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            暂无执行历史
          </div>
        ) : (
          <div className="space-y-3">
            {runs.map((run, idx) => (
              <div key={idx} className="rounded-lg border bg-white p-3 shadow-sm">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold">
                    Run #{runs.length - idx}
                  </span>
                  <span className="flex items-center gap-1 text-gray-400">
                    <Clock className="h-3 w-3" />
                    {formatDuration(run.totalTimeMs)}
                  </span>
                </div>

                <div className="mt-2 text-[10px] text-gray-400">
                  {run.startTime
                    ? new Date(run.startTime).toLocaleString()
                    : "N/A"}
                  {" · "}
                  {run.events.length} nodes
                  {run.errors > 0 && (
                    <span className="ml-1 text-red-500">
                      ({run.errors} errors)
                    </span>
                  )}
                </div>

                <div className="mt-2 flex gap-1">
                  {run.events.map((event) => (
                    <div
                      key={event.id}
                      title={`${event.nodeId}: ${event.executionTimeMs}ms`}
                      className={`h-2 flex-1 rounded-sm ${
                        event.status === "error"
                          ? "bg-red-400"
                          : event.status === "done"
                          ? "bg-green-400"
                          : "bg-blue-400"
                      }`}
                    />
                  ))}
                </div>

                <button
                  onClick={() => {
                    const firstIdx = events.findIndex(
                      (e) => e.id === run.events[0]?.id
                    );
                    if (firstIdx >= 0) setReplayIndex(firstIdx);
                  }}
                  className="mt-2 flex items-center gap-1 text-[10px] text-blue-600 hover:text-blue-800"
                >
                  <Play className="h-3 w-3" />
                  回放此执行
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
