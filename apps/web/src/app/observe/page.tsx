"use client";

import { useState, useEffect } from "react";
import { HarnessSelector } from "@/components/observe/HarnessSelector";
import { SessionList } from "@/components/observe/SessionList";
import { EventTimeline } from "@/components/observe/EventTimeline";
import { ChatInterface } from "@/components/observe/ChatInterface";
import { ObserveEvent, HarnessType, observeApi } from "@/lib/observe-api";

export default function ObservePage() {
  const [selectedHarness, setSelectedHarness] = useState<HarnessType | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [events, setEvents] = useState<ObserveEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [ws, setWs] = useState<WebSocket | null>(null);

  // 获取历史事件（replay）
  useEffect(() => {
    if (!selectedHarness || !selectedSession) {
      setEvents([]);
      return;
    }

    let cancelled = false;
    const loadHistory = async () => {
      try {
        const historyEvents = await observeApi.getReplayEvents(
          selectedHarness,
          selectedSession
        );
        if (!cancelled) {
          setEvents(historyEvents);
        }
      } catch (error) {
        console.error("Failed to load history:", error);
      }
    };

    void loadHistory();

    return () => {
      cancelled = true;
    };
  }, [selectedHarness, selectedSession]);

  // WebSocket 订阅实时事件
  useEffect(() => {
    if (!selectedHarness || !selectedSession) {
      setIsConnected(false);
      return;
    }

    const wsUrl = observeApi.getWebSocketUrl(selectedHarness, selectedSession);
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      setIsConnected(true);
      console.log("WebSocket connected");
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "event" && data.payload) {
          const newEvent: ObserveEvent = data.payload;
          setEvents((prev) => [...prev, newEvent]);
        }
      } catch (error) {
        console.error("Failed to parse WS message:", error);
      }
    };

    socket.onerror = (error) => {
      console.error("WebSocket error:", error);
      setIsConnected(false);
    };

    socket.onclose = () => {
      setIsConnected(false);
      console.log("WebSocket disconnected");
    };

    setWs(socket);

    return () => {
      socket.close();
    };
  }, [selectedHarness, selectedSession]);

  const handleSendMessage = async (message: string) => {
    if (!selectedHarness || !selectedSession) {
      alert("请先选择 harness 和 session");
      return;
    }

    try {
      await observeApi.sendMessage({
        harness_type: selectedHarness,
        session_id: selectedSession,
        message,
      });
    } catch (error) {
      console.error("Failed to send message:", error);
      alert("发送消息失败");
    }
  };

  return (
    <div className="flex h-full">
      {/* Left sidebar: harness + session selection */}
      <div className="w-64 border-r bg-gray-50">
        <div className="border-b p-4">
          <h2 className="text-lg font-semibold">观测操作台</h2>
          <p className="mt-1 text-xs text-gray-500">
            多 harness turn 观测
          </p>
        </div>
        <div className="p-4">
          <HarnessSelector
            selectedHarness={selectedHarness}
            onSelect={setSelectedHarness}
            onSessionChange={setSelectedSession}
          />
          {selectedHarness && (
            <SessionList
              harnessType={selectedHarness}
              selectedSession={selectedSession}
              onSelectSession={(sessionId) => {
                setSelectedSession(sessionId);
                setEvents([]); // Clear events when switching sessions
              }}
            />
          )}
        </div>
      </div>

      {/* Main area: event timeline + chat */}
      <div className="flex flex-1 flex-col">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-6 py-3">
          <div className="flex items-center gap-3">
            <h3 className="font-semibold">
              {selectedHarness ? `${selectedHarness} - ${selectedSession?.slice(0, 8) || "未选择"}` : "未选择"}
            </h3>
            <div className="flex items-center gap-1.5 text-xs">
              <div
                className={`h-2 w-2 rounded-full ${
                  isConnected ? "bg-green-500" : "bg-gray-300"
                }`}
              />
              <span className={isConnected ? "text-green-600" : "text-gray-400"}>
                {isConnected ? "实时连接" : "离线"}
              </span>
            </div>
          </div>
          <div className="text-sm text-gray-500">
            事件: {events.length}
          </div>
        </div>

        {/* Event timeline */}
        <div className="flex-1 overflow-y-auto">
          <EventTimeline events={events} />
        </div>

        {/* Chat input */}
        <div className="border-t">
          <ChatInterface
            onSendMessage={handleSendMessage}
            disabled={!selectedHarness || !selectedSession}
          />
        </div>
      </div>
    </div>
  );
}
