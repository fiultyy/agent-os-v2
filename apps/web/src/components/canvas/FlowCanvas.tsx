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

const initialNodes: Node[] = [
  { id: "1", type: "agent", position: { x: 250, y: 50 }, data: { label: "Agent" } },
];

const initialEdges: Edge[] = [];

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
