export interface Agent {
  id: string;
  name: string;
  description?: string;
  status: "idle" | "running" | "error" | "stopped";
  model?: string;
  tools?: string[];
  createdAt: string;
  updatedAt: string;
}

export interface AgentExecution {
  id: string;
  agentId: string;
  input: string;
  output?: string;
  status: "pending" | "running" | "completed" | "failed";
  startedAt: string;
  completedAt?: string;
}
