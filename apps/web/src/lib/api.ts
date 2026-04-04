import type { AgentItem } from "@/stores/agentStore";

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
