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
  created_at?: string;
}

export type EventType =
  | "tick_started"
  | "tool_call"
  | "tool_result"
  | "tick_completed"
  | "branch_created"
  | "branch_merged";

/**
 * Session 信息（对齐 observe-service session_store 实际字段）
 * 服务端返回:harness_type / session_id / harness_id / created_at / last_active
 * 注意:无 last_event_at,无 event_count(前端按需可选)。
 */
export interface SessionInfo {
  harness_type: HarnessType;
  session_id: string;
  harness_id: string;
  created_at: string;
  last_active: string;
  event_count?: number;
}

/**
 * 分组 Session 列表响应(GET /sessions/grouped 返回 {sessions_by_harness: {...}})
 */
export interface GroupedSessions {
  sessions_by_harness: Record<string, SessionInfo[]>;
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
   * GET /sessions/grouped 返回 {sessions_by_harness: {<harness>: [sessions]}}
   */
  async getGroupedSessions(): Promise<GroupedSessions> {
    const res = await fetch(`${OBSERVE_BASE}/sessions/grouped`);
    if (!res.ok) {
      throw new Error(`Failed to fetch sessions: ${res.status}`);
    }
    const data = await res.json();
    return {
      sessions_by_harness: data?.sessions_by_harness ?? {},
    };
  },

  /**
   * 获取指定 harness 的 session 列表
   * GET /sessions?harness_type=<h> 返回 {sessions: [...]}
   */
  async getSessions(harnessType: HarnessType): Promise<SessionInfo[]> {
    const res = await fetch(`${OBSERVE_BASE}/sessions?harness_type=${harnessType}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch sessions: ${res.status}`);
    }
    const data = await res.json();
    const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
    return sessions as SessionInfo[];
  },

  /**
   * 获取历史事件（replay）
   * GET /sessions/<h>/<sid>/events 返回 {events: [...]}
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
    const data = await res.json();
    const events = Array.isArray(data?.events) ? data.events : [];
    return events as ObserveEvent[];
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
