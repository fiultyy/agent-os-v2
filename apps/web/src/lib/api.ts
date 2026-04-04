import type { AgentItem } from "@/stores/agentStore";
import type { MemoryItem } from "@/stores/memoryStore";
import type { CommMessage, ExecutionEvent } from "@/stores/debugStore";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

/** Map snake_case backend response to camelCase AgentItem. */
function mapAgent(raw: Record<string, unknown>): AgentItem {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    description: String(raw.description ?? ""),
    status: (raw.status as AgentItem["status"]) ?? "idle",
    model: String(raw.model ?? ""),
    tools: (raw.tools as string[]) ?? [],
    createdAt: String(raw.created_at ?? raw.createdAt ?? ""),
    updatedAt: String(raw.updated_at ?? raw.updatedAt ?? ""),
  };
}

/** Map snake_case backend response to camelCase MemoryItem. */
function mapMemory(raw: Record<string, unknown>): MemoryItem {
  return {
    id: String(raw.id ?? ""),
    agentId: String(raw.agent_id ?? raw.agentId ?? ""),
    sessionId: String(raw.session_id ?? raw.sessionId ?? ""),
    memoryType: (raw.memory_type ?? raw.memoryType ?? "session") as MemoryItem["memoryType"],
    scope: (raw.scope ?? "agent") as MemoryItem["scope"],
    content: String(raw.content ?? ""),
    importance: Number(raw.importance ?? 0.5),
    metadata: (raw.metadata ?? raw.metadata_json ? JSON.parse(String(raw.metadata_json)) : {}) as Record<string, unknown>,
    createdAt: String(raw.created_at ?? raw.createdAt ?? ""),
    accessedAt: String(raw.accessed_at ?? raw.accessedAt ?? ""),
    archived: Boolean(raw.archived ?? false),
  };
}

function mapCommMessage(raw: Record<string, unknown>): CommMessage {
  return {
    id: String(raw.id ?? ""),
    senderId: String(raw.sender_id ?? raw.senderId ?? ""),
    recipientId: raw.recipient_id != null ? String(raw.recipient_id) : null,
    sessionId: String(raw.session_id ?? raw.sessionId ?? ""),
    workspaceId: String(raw.workspace_id ?? raw.workspaceId ?? ""),
    messageType: String(raw.message_type ?? raw.messageType ?? "task"),
    content: String(raw.content ?? ""),
    payload: (raw.payload ?? {}) as Record<string, unknown>,
    correlationId: raw.correlation_id != null ? String(raw.correlation_id) : null,
    timestamp: String(raw.timestamp ?? ""),
    priority: Number(raw.priority ?? 1),
    deliveryStatus: String(raw.delivery_status ?? "pending"),
  };
}

// ── Agent CRUD ────────────────────────────────────────────────

export async function getAgents(): Promise<AgentItem[]> {
  const raw = await request<Record<string, unknown>[]>("/agents/");
  return raw.map(mapAgent);
}

export async function createAgent(data: {
  name: string;
  description?: string;
  model?: string;
  tools?: string[];
}): Promise<AgentItem> {
  const raw = await request<Record<string, unknown>>("/agents/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return mapAgent(raw);
}

export async function getAgent(id: string): Promise<AgentItem> {
  const raw = await request<Record<string, unknown>>(`/agents/${id}`);
  return mapAgent(raw);
}

export async function deleteAgent(id: string): Promise<void> {
  await request(`/agents/${id}`, { method: "DELETE" });
}

// ── Execute (SSE) ─────────────────────────────────────────────

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export async function executeWithSSE(
  agentId: string,
  input: string,
  sessionId?: string,
  onEvent?: (event: SSEEvent) => void
): Promise<void> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, session_id: sessionId || "" }),
  });

  if (!res.ok || !res.body) {
    throw new Error(`Execute failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEvent = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7);
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        if (dataStr === "[DONE]") return;
        try {
          const data = JSON.parse(dataStr);
          onEvent?.({ event: currentEvent, data });
        } catch {
          // skip non-JSON data lines
        }
        currentEvent = "";
      }
    }
  }
}

// ── Memory API ────────────────────────────────────────────────

export async function getMemories(
  agentId?: string,
  memoryType?: string,
  sessionId?: string,
  limit: number = 100
): Promise<MemoryItem[]> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  if (memoryType) params.set("memory_type", memoryType);
  if (sessionId) params.set("session_id", sessionId);
  params.set("limit", String(limit));

  const raw = await request<Record<string, unknown>[]>(`/memories/?${params}`);
  return raw.map(mapMemory);
}

export async function getMemoryLayers(agentId: string): Promise<Record<string, number>> {
  const raw = await request<Record<string, unknown>>(`/memories/layers?agent_id=${agentId}`);
  return raw as Record<string, number>;
}

export async function storeMemory(data: {
  content: string;
  agent_id: string;
  session_id?: string;
  memory_type?: string;
  scope?: string;
  importance?: number;
}): Promise<MemoryItem> {
  const raw = await request<Record<string, unknown>>("/memories/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return mapMemory(raw);
}

export async function deleteMemory(id: string): Promise<void> {
  await request(`/memories/${id}`, { method: "DELETE" });
}

// ── Communication API ─────────────────────────────────────────

export async function getMessages(
  agentId?: string,
  sessionId?: string,
  limit: number = 50
): Promise<CommMessage[]> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  if (sessionId) params.set("session_id", sessionId);
  params.set("limit", String(limit));

  const raw = await request<Record<string, unknown>[]>(`/messages/?${params}`);
  return raw.map(mapCommMessage);
}

export async function sendMessage(data: {
  sender_id: string;
  recipient_id?: string;
  session_id?: string;
  content: string;
  message_type?: string;
  priority?: number;
}): Promise<CommMessage> {
  const raw = await request<Record<string, unknown>>("/messages/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return mapCommMessage(raw);
}

// ── Debug / Execution History API ─────────────────────────────

export async function getExecutionHistory(
  agentId?: string,
  sessionId?: string,
  limit: number = 50
): Promise<ExecutionEvent[]> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  if (sessionId) params.set("session_id", sessionId);
  params.set("limit", String(limit));

  const raw = await request<Record<string, unknown>[]>(`/debug/history?${params}`);
  return raw.map((r) => ({
    id: String(r.id ?? ""),
    nodeId: String(r.node_id ?? r.nodeId ?? ""),
    agentId: String(r.agent_id ?? r.agentId ?? ""),
    status: (r.status ?? "done") as ExecutionEvent["status"],
    input: String(r.input ?? ""),
    output: String(r.output ?? ""),
    executionTimeMs: Number(r.execution_time_ms ?? r.executionTimeMs ?? 0),
    timestamp: String(r.timestamp ?? ""),
    metadata: (r.metadata ?? {}) as Record<string, unknown>,
  }));
}

// ── Knowledge Graph API ───────────────────────────────────────

export async function searchEntities(query: string, limit: number = 20): Promise<Record<string, unknown>[]> {
  const params = new URLSearchParams();
  params.set("q", query);
  params.set("limit", String(limit));
  return request(`/kg/entities?${params}`);
}

export async function expandEntity(name: string, depth: number = 2): Promise<Record<string, unknown>> {
  return request(`/kg/expand?name=${encodeURIComponent(name)}&depth=${depth}`);
}
