import { ObserveEvent, EventType } from "@/lib/observe-api";
import {
  Play,
  CheckCircle2,
  XCircle,
  Wrench,
  ArrowRight,
  GitBranch,
  GitMerge,
  Clock,
} from "lucide-react";

interface EventTimelineProps {
  events: ObserveEvent[];
}

const EVENT_TYPE_CONFIG: Record<
  EventType,
  { icon: React.ReactNode; color: string; label: string }
> = {
  tick_started: {
    icon: <Play className="h-3.5 w-3.5" />,
    color: "text-blue-500",
    label: "Turn 开始",
  },
  tool_call: {
    icon: <Wrench className="h-3.5 w-3.5" />,
    color: "text-amber-500",
    label: "工具调用",
  },
  tool_result: {
    icon: <ArrowRight className="h-3.5 w-3.5" />,
    color: "text-purple-500",
    label: "工具结果",
  },
  tick_completed: {
    icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    color: "text-green-500",
    label: "Turn 完成",
  },
  branch_created: {
    icon: <GitBranch className="h-3.5 w-3.5" />,
    color: "text-cyan-500",
    label: "分支创建",
  },
  branch_merged: {
    icon: <GitMerge className="h-3.5 w-3.5" />,
    color: "text-indigo-500",
    label: "分支合并",
  },
};

function EventCard({ event, index }: { event: ObserveEvent; index: number }) {
  const config = EVENT_TYPE_CONFIG[event.event_type as EventType];
  const isTickComplete = event.event_type === "tick_completed";
  const status = isTickComplete && (event.data.status as string) === "error";

  return (
    <div
      className={`rounded-lg border p-3 transition-colors ${
        isTickComplete && status
          ? "border-red-200 bg-red-50"
          : "border-gray-200 bg-white hover:bg-gray-50"
      }`}
    >
      <div className="flex items-start gap-2">
        <div className={`mt-0.5 ${config.color}`}>
          {config.icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">{config.label}</span>
            <span className="text-xs text-gray-400">
              #{index + 1}
            </span>
            {isTickComplete && status && (
              <XCircle className="h-3 w-3 text-red-500" />
            )}
          </div>

          {/* Event-specific content */}
          {event.event_type === "tick_started" && (
            <div className="mt-2 truncate text-xs text-gray-600">
              {(event.data.request as string)?.slice(0, 100)}
            </div>
          )}

          {event.event_type === "tool_call" && (
            <div className="mt-2 space-y-1">
              <div className="font-mono text-xs font-medium text-amber-700">
                {event.data.tool_name as string}
              </div>
              <div className="max-h-20 overflow-auto rounded bg-gray-50 p-2 text-[10px] text-gray-700">
                <pre>{JSON.stringify(event.data.arguments, null, 2)}</pre>
              </div>
            </div>
          )}

          {event.event_type === "tool_result" && (
            <div className="mt-2 space-y-1">
              {event.data.error ? (
                <div className="text-xs text-red-600">
                  Error: {event.data.error as string}
                </div>
              ) : (
                <div className="max-h-32 overflow-auto rounded bg-gray-50 p-2 text-[10px] text-gray-700">
                  <pre>{(event.data.result as string)?.slice(0, 500)}</pre>
                </div>
              )}
            </div>
          )}

          {event.event_type === "tick_completed" && (
            <div className="mt-2 space-y-1">
              <div className="flex items-center gap-3 text-xs text-gray-600">
                <span>
                  状态:{" "}
                  <span
                    className={
                      (event.data.status as string) === "success"
                        ? "text-green-600"
                        : "text-red-600"
                    }
                  >
                    {event.data.status as string}
                  </span>
                </span>
                <span>
                  工具: {event.data.tool_count as number}
                </span>
                <span>
                  耗时: {Math.round((event.data.duration_ms as number) || 0)}ms
                </span>
              </div>
              {(event.data.response as string) && (
                <div className="mt-1 max-h-24 overflow-auto rounded bg-gray-50 p-2 text-[10px] text-gray-700">
                  <pre>{(event.data.response as string)?.slice(0, 300)}</pre>
                </div>
              )}
            </div>
          )}

          {/* Timestamp */}
          <div className="mt-2 flex items-center gap-1 text-xs text-gray-400">
            <Clock className="h-3 w-3" />
            {new Date(event.timestamp).toLocaleTimeString("zh-CN")}
            {event.tick_id && (
              <span className="ml-2 font-mono text-gray-500">
                tick: {event.tick_id.slice(0, 8)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function EventTimeline({ events }: EventTimelineProps) {
  if (events.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center text-sm text-gray-400">
          <p>暂无事件</p>
          <p className="mt-1 text-xs">选择 harness 和 session 后开始观测</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4">
      <div className="mb-3 text-sm font-semibold text-gray-700">Turn 事件流</div>
      <div className="space-y-2">
        {events.map((event, index) => (
          <EventCard key={event.event_id} event={event} index={index} />
        ))}
      </div>
    </div>
  );
}
