import type { Node, Edge } from "@xyflow/react";

export interface FlowData {
  nodes: Node[];
  edges: Edge[];
}

export type FlowNodeType = "agent" | "tool" | "prompt";
export type FlowEdgeType = "data";
