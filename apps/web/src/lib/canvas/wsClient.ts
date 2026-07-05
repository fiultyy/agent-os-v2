// P1-5: WebSocket Event Client — receives canvas events, replays to React Flow
"use client";

import type { CanvasEvent } from "@/types/canvas";
import { useCanvasStore } from "../../stores/canvasStore";

export class CanvasWSClient {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private sessionId: string = "";
  private token: string = "";
  private reconnectDelay: number = 1000; // ms, exponential backoff
  private static readonly MAX_RECONNECT_DELAY = 30000; // 30s cap
  private static readonly HEARTBEAT_INTERVAL_MS = 30000; // 心跳间隔(canvas.py ping/pong 保活)
  private static readonly NON_RECONNECTABLE_CODES = new Set([4001, 4003]);

  connect(sessionId: string, afterEventId?: string, token?: string): void {
    // 切换 session:彻底断旧连接 + 清 store。canvasStore 是模块级单例(不随页面
    // unmount 重置),旧 session 的 events/ticks 不清会把多个 session 混在一个页面;
    // 旧 ws 不 close 会泄漏,且其 onclose 会 scheduleReconnect 重连旧 session(双重订阅)。
    if (this.sessionId && this.sessionId !== sessionId) {
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      this.stopHeartbeat();
      if (this.ws) {
        this.ws.onclose = null; // 防 close → scheduleReconnect 重连旧 session
        this.ws.close();
        this.ws = null;
      }
      useCanvasStore.getState().clearEvents();
      console.log("[CanvasWS] session 切换,清旧事件 + 断旧连接");
    }
    this.sessionId = sessionId;
    this.token = token || "";

    const params = new URLSearchParams({ session_id: sessionId });
    if (afterEventId) params.set("after_event_id", afterEventId);
    if (this.token) params.set("token", this.token);

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/canvas?${params}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log("[CanvasWS] Connected to", sessionId);
      // Reset backoff on successful connection
      this.reconnectDelay = 1000;
      const store = useCanvasStore.getState();
      store.setConnected(true);
      // Write the connected sessionId back into the store so downstream
      // (BranchManager HTTP calls, tabs) always have a non-empty session_id
      // even when the page was opened without ?session_id=.
      store.setSessionId(sessionId);
      // 心跳保活:连接后立即发 ping + 30s 周期(canvas.py 收 {"cmd":"ping"} 回 {"cmd":"pong"})。
      // 双向通路验证 + 防 idle timeout + ws 观测面收到 pong 帧(c6a6a31 deferred 心跳项补完)。
      this.sendHeartbeat();
      this.heartbeatTimer = setInterval(
        () => this.sendHeartbeat(),
        CanvasWSClient.HEARTBEAT_INTERVAL_MS,
      );
    };

    this.ws.onmessage = (event) => {
      try {
        const raw = JSON.parse(event.data);
        // 只把 canvas event(有 type 字段)进 store;
        // 控制消息(cmd/status/error,如 pong/ack/layer2.submitted)忽略,不污染 events。
        if (raw && typeof raw.type === "string") {
          useCanvasStore.getState().addEvent(raw as CanvasEvent);
        }
      } catch (e) {
        console.error("[CanvasWS] Parse error:", e);
      }
    };

    this.ws.onerror = (err) => console.error("[CanvasWS] Error:", err);

    this.ws.onclose = (event) => {
      this.stopHeartbeat();
      useCanvasStore.getState().setConnected(false);
      // Don't reconnect on auth/origin rejection
      if (CanvasWSClient.NON_RECONNECTABLE_CODES.has(event.code)) {
        console.warn("[CanvasWS] Non-reconnectable close code:", event.code, event.reason);
        return;
      }
      this.scheduleReconnect(afterEventId);
    };
  }

  /** 发心跳 ping(server 回 pong;仅 OPEN 状态发)。 */
  private sendHeartbeat(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ cmd: "ping" }));
    }
  }

  /** 停心跳(连接关闭/断开时清 interval,避免对已关闭 ws 发送)。 */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect(afterEventId?: string): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const delay = this.reconnectDelay;
    // Exponential backoff with jitter
    this.reconnectDelay = Math.min(
      this.reconnectDelay * 2 + Math.random() * 500,
      CanvasWSClient.MAX_RECONNECT_DELAY,
    );
    console.log(`[CanvasWS] Reconnecting in ${Math.round(delay)}ms`);
    this.reconnectTimer = setTimeout(() => {
      const events = useCanvasStore.getState().events;
      const lastEvent = events[events.length - 1];
      const lastEventId = lastEvent?.event_id || afterEventId || "";
      console.log("[CanvasWS] Reconnecting after", lastEventId);
      this.connect(this.sessionId, lastEventId, this.token);
    }, delay);
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopHeartbeat();
    this.ws?.close();
    this.ws = null;
    useCanvasStore.getState().setConnected(false);
  }

  send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }
}

export const canvasWsClient = new CanvasWSClient();
