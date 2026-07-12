/**
 * Observe-Service API Client
 *
 * 对接 observe-service (端口 8002) 的 WebSocket 和 REST API。
 * 协议参考: services/observe/protocol/SCHEMA.md
 */

export type HarnessType = "claude-code" | "openclaw" | "agent-os-v2";

/**
 * Observe Event Schema (统一 turn 事件)
 */
export interface ObserveEvent {
  event_id: string;
  harness_type: string;
  harness_id: string;
  session_id: string;
  tick_id: string;
  event_type: EventType;
  data: Record<string, unknown>;
  timestamp: string;
}

export type EventType =
  | "tick_started"
  | "tool_call"
  | "tool_result"
  | "tick_completed"
  | "branch_created"
  | "branch_merged";

/**
 * Session 信息（按 harness 分组）
 */
export interface SessionInfo {
  harness_type: HarnessType;
  session_id: string;
  created_at: string;
  last_event_at: string;
  event_count: number;
}

/**
 * 分组 Session 列表响应
 */
export interface GroupedSessions {
  [harnessType: string]: SessionInfo[];
}

const OBSERVE_BASE = process.env.NEXT_PUBLIC_OBSERVE_URL || "http://localhost:8002";

/**
 * Observe-Service API 客户端
 */
export const observeApi = {
  /**
   * 获取 WebSocket 订阅 URL
   */
  getWebSocketUrl(
    harnessType: HarnessType,
    sessionId: string,
    afterEventId?: string
  ): string {
    const wsBase = OBSERVE_BASE.replace("http://", "ws://").replace("https://", "wss://");
    const params = new URLSearchParams({
      harness_type: harnessType,
      session_id: sessionId,
    });
    if (afterEventId) {
      params.set("after_event_id", afterEventId);
    }
    return `${wsBase}/ws/subscribe?${params.toString()}`;
  },

  /**
   * 获取分组 session 列表
   */
  async getGroupedSessions(): Promise<GroupedSessions> {
    const res = await fetch(`${OBSERVE_BASE}/sessions/grouped`);
    if (!res.ok) {
      throw new Error(`Failed to fetch sessions: ${res.status}`);
    }
    return res.json();
  },

  /**
   * 获取指定 harness 的 session 列表
   */
  async getSessions(harnessType: HarnessType): Promise<SessionInfo[]> {
    const res = await fetch(`${OBSERVE_BASE}/sessions?harness_type=${harnessType}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch sessions: ${res.status}`);
    }
    return res.json();
  },

  /**
   * 获取历史事件（replay）
   */
  async getReplayEvents(
    harnessType: HarnessType,
    sessionId: string,
    afterEventId?: string
  ): Promise<ObserveEvent[]> {
    const params = new URLSearchParams();
    if (afterEventId) {
      params.set("after_event_id", afterEventId);
    }
    const res = await fetch(
      `${OBSERVE_BASE}/sessions/${harnessType}/${sessionId}/events?${params.toString()}`
    );
    if (!res.ok) {
      throw new Error(`Failed to fetch replay events: ${res.status}`);
    }
    return res.json();
  },

  /**
   * 发送消息到 harness（经 observe-service 路由）
   */
  async sendMessage(payload: {
    harness_type: HarnessType;
    session_id: string;
    message: string;
  }): Promise<void> {
    const res = await fetch(`${OBSERVE_BASE}/send`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      throw new Error(`Failed to send message: ${res.status}`);
    }
  },

  /**
   * 健康检查
   */
  async healthCheck(): Promise<{ status: string; version: string }> {
    const res = await fetch(`${OBSERVE_BASE}/health`);
    if (!res.ok) {
      throw new Error(`Health check failed: ${res.status}`);
    }
    return res.json();
  },
};
