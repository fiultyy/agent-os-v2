"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/Header";
import { TickCanvas } from "@/components/canvas/TickCanvas";
import { BranchManager } from "@/components/canvas/BranchManager";
import { Layer2Panel } from "@/components/canvas/Layer2Panel";
import { ScoringOverlay } from "@/components/canvas/ScoringOverlay";
import { useCanvasStore } from "@/stores/canvasStore";
import { canvasWsClient } from "@/lib/canvas/wsClient";
import { Wifi, WifiOff } from "lucide-react";

export default function CanvasLivePage() {
  const connected = useCanvasStore(s => s.connected);
  const sessionId = useCanvasStore(s => s.sessionId);
  const setSessionId = useCanvasStore(s => s.setSessionId);
  const [inputSessionId, setInputSessionId] = useState("");

  // WS 连接管理：从 URL 参数或 store 读取 session_id；保证 sid 非空以闭环
  // BranchManager 的 session_id（之前 store.sessionId 常为 null → branch 创建恒空）。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("session_id") || sessionId;
    if (sid) {
      // Write into store up-front so synchronous consumers (BranchManager HTTP
      // calls fired before ws.onopen) already see a non-empty session_id.
      setSessionId(sid);
      canvasWsClient.connect(sid);
    }
    // 不在 unmount 时断连，保持后台运行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Session 输入 → 连接
  const handleConnect = () => {
    const sid = inputSessionId.trim();
    if (sid) {
      setSessionId(sid);
      canvasWsClient.connect(sid);
    }
  };

  return (
    <div className="flex h-screen w-screen flex-col">
      <Header />
      {/* 连接状态栏 */}
      <div className="flex items-center gap-2 px-4 py-2 border-b">
        {connected
          ? <Wifi className="text-green-500" />
          : <WifiOff className="text-red-500" />}
        <span className="text-sm text-gray-600">
          {connected ? `已连接: ${sessionId}` : "未连接"}
        </span>
        {/* Session 输入 */}
        <input
          className="ml-4 border rounded px-2 py-1 text-sm"
          placeholder="输入 Session ID"
          value={inputSessionId}
          onChange={e => setInputSessionId(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleConnect()}
        />
        <button
          className="bg-blue-500 text-white px-3 py-1 rounded text-sm"
          onClick={handleConnect}
        >
          连接
        </button>
      </div>
      {/* 主内容区：BranchManager 侧边 + TickCanvas 主区 + Layer2Panel 右侧 */}
      <div className="flex flex-1 overflow-hidden">
        <BranchManager />
        <div className="flex-1 relative">
          <TickCanvas />
          <ScoringOverlay />
        </div>
        <Layer2Panel />
      </div>
    </div>
  );
}
