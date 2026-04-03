"use client";

import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AgentNode } from "./nodes/AgentNode";
import { ToolNode } from "./nodes/ToolNode";
import { PromptNode } from "./nodes/PromptNode";
import { DataEdge } from "./edges/DataEdge";

const nodeTypes = {
  agent: AgentNode,
  tool: ToolNode,
  prompt: PromptNode,
};

const edgeTypes = {
  data: DataEdge,
};

/** Hard-coded demo nodes: 1 Agent + 1 Tool connected by an edge. */
const initialNodes: Node[] = [
  {
    id: "agent-1",
    type: "agent",
    position: { x: 250, y: 50 },
    data: {
      label: "Research Agent",
      status: "idle",
      memoryCount: 0,
    },
  },
  {
    id: "tool-1",
    type: "tool",
    position: { x: 250, y: 250 },
    data: {
      label: "Web Search",
      lastStatus: "idle",
      duration: 0,
    },
  },
];

const initialEdges: Edge[] = [
  {
    id: "e-agent-1-tool-1",
    source: "agent-1",
    target: "tool-1",
    type: "data",
  },
];

export function FlowCanvas() {
  return (
    <ReactFlow
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      defaultNodes={initialNodes}
      defaultEdges={initialEdges}
      fitView
    >
      <Background />
      <Controls />
      <MiniMap />
    </ReactFlow>
  );
}
