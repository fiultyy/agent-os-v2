// P1-5: WebSocket Event Client — receives canvas events, replays to React Flow
"use client";

import type { CanvasEvent } from "@/types/canvas";
import { useCanvasStore } from "../../stores/canvasStore";

export class CanvasWSClient {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private sessionId: string = "";
  private token: string = "";
  private reconnectDelay: number = 1000; // ms, exponential backoff
  private static readonly MAX_RECONNECT_DELAY = 30000; // 30s cap
  private static readonly NON_RECONNECTABLE_CODES = new Set([4001, 4003]);

  connect(sessionId: string, afterEventId?: string, token?: string): void {
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

    this.ws.onclose = (event) => {
      useCanvasStore.getState().setConnected(false);
      // Don't reconnect on auth/origin rejection
      if (CanvasWSClient.NON_RECONNECTABLE_CODES.has(event.code)) {
        console.warn("[CanvasWS] Non-reconnectable close code:", event.code, event.reason);
        return;
      }
      this.scheduleReconnect(afterEventId);
    };
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
