// P1-5: WebSocket Event Client — receives canvas events, replays to React Flow
"use client";

import type { CanvasEvent } from "@/types/canvas";
import { useCanvasStore } from "../../stores/canvasStore";

export class CanvasWSClient {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private sessionId: string = "";

  connect(sessionId: string, afterEventId?: string): void {
    this.sessionId = sessionId;

    const params = new URLSearchParams({ session_id: sessionId });
    if (afterEventId) params.set("after_event_id", afterEventId);

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/canvas?${params}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log("[CanvasWS] Connected to", sessionId);
      useCanvasStore.getState().setConnected(true);
    };

    this.ws.onmessage = (event) => {
      try {
        const evt: CanvasEvent = JSON.parse(event.data);
        useCanvasStore.getState().addEvent(evt);
      } catch (e) {
        console.error("[CanvasWS] Parse error:", e);
      }
    };

    this.ws.onerror = (err) => console.error("[CanvasWS] Error:", err);

    this.ws.onclose = () => {
      useCanvasStore.getState().setConnected(false);
      this.scheduleReconnect(afterEventId);
    };
  }

  private scheduleReconnect(afterEventId?: string): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      const events = useCanvasStore.getState().events;
      const lastEvent = events[events.length - 1];
      const lastEventId = lastEvent?.event_id || afterEventId || "";
      console.log("[CanvasWS] Reconnecting after", lastEventId);
      this.connect(this.sessionId, lastEventId);
    }, 3000);
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
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
