"use client";

import { useState, useEffect } from "react";
import { HarnessType, observeApi, SessionInfo } from "@/lib/observe-api";
import { Clock, Calendar } from "lucide-react";

interface SessionListProps {
  harnessType: HarnessType;
  selectedSession: string | null;
  onSelectSession: (sessionId: string) => void;
}

export function SessionList({
  harnessType,
  selectedSession,
  onSelectSession,
}: SessionListProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadSessions = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await observeApi.getSessions(harnessType);
        if (!cancelled) {
          setSessions(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadSessions();

    return () => {
      cancelled = true;
    };
  }, [harnessType]);

  const formatTime = (isoString: string) => {
    const date = new Date(isoString);
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const formatDate = (isoString: string) => {
    const date = new Date(isoString);
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return "今天";
    }
    return date.toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    });
  };

  if (loading) {
    return (
      <div className="mt-4 text-center text-xs text-gray-400">加载中...</div>
    );
  }

  if (error) {
    return (
      <div className="mt-4 text-center text-xs text-red-500">{error}</div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="mt-4 text-center text-xs text-gray-400">
        暂无 session
      </div>
    );
  }

  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-700">Sessions</div>
        <div className="text-xs text-gray-500">{sessions.length}</div>
      </div>
      <div className="max-h-96 space-y-1 overflow-y-auto">
        {sessions.map((session) => (
          <button
            key={session.session_id}
            onClick={() => onSelectSession(session.session_id)}
            className={`w-full rounded-lg border p-2.5 text-left transition-colors ${
              selectedSession === session.session_id
                ? "border-blue-500 bg-blue-50"
                : "border-gray-200 bg-white hover:bg-gray-50"
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="truncate font-mono text-xs">
                {session.session_id.slice(0, 12)}
              </div>
              <div className="flex items-center gap-1 text-xs text-gray-500">
                <span className="flex items-center gap-0.5">
                  <Calendar className="h-3 w-3" />
                  {formatDate(session.created_at)}
                </span>
                <span className="flex items-center gap-0.5">
                  <Clock className="h-3 w-3" />
                  {formatTime(session.last_event_at)}
                </span>
              </div>
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {session.event_count} 事件
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
