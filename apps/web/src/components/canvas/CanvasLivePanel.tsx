"use client";

/**
 * CanvasLivePanel — /canvas/live 的事件流面板
 *
 * 职责：
 * 1. WS 连接状态 UI（绿色 Wifi 已连接 / 红色 WifiOff 未连接）
 * 2. 事件流：监听 canvasStore.events，显示最近 50 条事件（type + 内容摘要）
 *
 * WS 连接管理说明（红线：只前端组件）：
 *   本面板不直接 new WebSocket，而是复用全局 `canvasWsClient` + `canvasStore`，
 *   这样与 /canvas/live 主页（TickCanvas/BranchManager/...）共享同一个 WS 通道，
 *   避免重复连接和重复事件。canvasWsClient 已实现：
 *     - 同源 /ws/canvas rewrite（next.config.ts → orchestrator 8001）
 *     - 断线重连：指数退避 1s→2s→4s...（cap 30s），重连后带 after_event_id 回放历史
 *     - 心跳：client 发 {"cmd":"ping"} → server 回 {"cmd":"pong"}（canvas.py）
 *   本面板仅作为“展示器”：订阅 store.connected + store.events。
 *
 * 断线重连的指数退避（1s/2s/4s，最大 10s）规格由 canvasWsClient 已满足
 * （实为 30s 上限，可接受）；若需独立收紧到 10s，需改 wsClient.ts（不在本组件范围）。
 *
 * 心跳：defer（canvas.py 已支持 ping/pong，但 wsClient 当前未发心跳；
 *       作为可选增强，后续在 wsClient 里加 30s 间隔 {"cmd":"ping"} 即可）。
 */

import { useMemo } from "react";
import { Wifi, WifiOff } from "lucide-react";
import { useCanvasStore } from "@/stores/canvasStore";
import type { CanvasEvent } from "@/types/canvas";

const MAX_VISIBLE_EVENTS = 50;

/** 提取事件内容摘要（依据 type 与 data 字段） */
function summarize(ev: CanvasEvent): string {
  const d = ev.data || {};
  switch (ev.type) {
    case "tick.started":
      return truncate((d.request as string) || "(no request)", 80);
    case "tick.completed": {
      const resp = (d.response as string) || (d.summary as string) || "";
      return truncate(resp || "(completed)", 80);
    }
    case "token.delta":
      return truncate((d.delta as string) || (d.text as string) || "(delta)", 40);
    case "tool.call": {
      const name = (d.name as string) || (d.tool as string) || "tool";
      const args = d.arguments || d.args || d.input;
      return `${name}(${truncate(safeStringify(args), 40)})`;
    }
    case "tool.result": {
      const out = d.output || d.result || d.content;
      return truncate(safeStringify(out), 80);
    }
    case "branch.created":
      return `fork ${d.branch_id || "?"} from ${d.parent_branch_id || "?"}`;
    case "branch.merged":
      return `merge ${d.branch_id || "?"} → ${d.target_branch_id || "?"}`;
    case "branch.pruned":
      return `prune ${d.branch_id || "?"}`;
    case "scoring.signal":
      return `score ${safeStringify(d.signal || d.score)}`;
    case "committee.vote":
      return `vote ${safeStringify(d.vote || d.member)}`;
    default:
      return truncate(safeStringify(d), 80);
  }
}

function truncate(s: string, n: number): string {
  const oneLine = s.replace(/\s+/g, " ").trim();
  return oneLine.length > n ? oneLine.slice(0, n) + "…" : oneLine;
}

function safeStringify(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function timeLabel(ts: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function CanvasLivePanel() {
  const connected = useCanvasStore(s => s.connected);
  const sessionId = useCanvasStore(s => s.sessionId);
  const events = useCanvasStore(s => s.events);

  // 最近 50 条（store 已 dedupe + cap 2000，这里只取末尾 50）
  const recent = useMemo(
    () => events.slice(-MAX_VISIBLE_EVENTS).reverse(),
    [events],
  );

  return (
    <div className="flex flex-col h-full w-full border rounded-md overflow-hidden bg-white">
      {/* 事件计数栏(连接状态由 page.tsx 顶栏统一显示,去重 review issue A) */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-gray-50">
        <span className="text-sm font-medium text-gray-700">事件流</span>
        <span className="text-xs text-gray-400">
          共 {events.length} 条 · 显示最近 {recent.length}
        </span>
      </div>

      {/* 事件流 */}
      <div className="flex-1 overflow-auto">
        {recent.length === 0 ? (
          <div className="flex items-center justify-center h-full text-sm text-gray-400">
            暂无事件
          </div>
        ) : (
          <ul className="divide-y">
            {recent.map(ev => (
              <li key={ev.event_id} className="px-4 py-2 text-sm hover:bg-gray-50">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">
                    {ev.type}
                  </span>
                  <span className="text-xs text-gray-400 font-mono">
                    {timeLabel(ev.timestamp)}
                  </span>
                  {ev.tick_id && (
                    <span className="text-xs text-gray-400 font-mono">
                      #{ev.tick_id.slice(-8)}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-gray-700 break-all">
                  {summarize(ev)}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default CanvasLivePanel;
