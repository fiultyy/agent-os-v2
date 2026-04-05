"use client";

import { useCallback, useRef } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
  type ReactFlowInstance,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { AgentNode } from "./nodes/AgentNode";
import { ToolNode } from "./nodes/ToolNode";
import { PromptNode } from "./nodes/PromptNode";
import { DataEdge } from "./edges/DataEdge";
import { useFlowStore } from "@/stores/flowStore";
import { useAgentStore } from "@/stores/agentStore";
import type { FlowNodeType } from "@/types/flow";

const nodeTypes = {
  agent: AgentNode,
  tool: ToolNode,
  prompt: PromptNode,
};

const edgeTypes = {
  data: DataEdge,
};

let nodeIdCounter = 0;

function getNextId(type: FlowNodeType): string {
  nodeIdCounter += 1;
  return `${type}-${nodeIdCounter}`;
}

function getDefaultData(type: FlowNodeType): Record<string, unknown> {
  switch (type) {
    case "agent":
      return { label: "New Agent", status: "idle", memoryCount: 0 };
    case "tool":
      return { label: "New Tool", lastStatus: "idle", duration: 0 };
    case "prompt":
      return { label: "New Prompt", variables: [] };
    default:
      return { label: "Node" };
  }
}

export function FlowCanvas() {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const rfInstance = useRef<ReactFlowInstance | null>(null);

  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);
  const onNodesChange = useFlowStore((s) => s.onNodesChange);
  const onEdgesChange = useFlowStore((s) => s.onEdgesChange);
  const onConnect = useFlowStore((s) => s.onConnect);
  const addNode = useFlowStore((s) => s.addNode);
  const setSelectedNodeId = useFlowStore((s) => s.setSelectedNodeId);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const addAgent = useAgentStore((s) => s.addAgent);
  // updateNodeData kept for external use; addNode handles API calls internally

  const onInit = useCallback((instance: ReactFlowInstance) => {
    rfInstance.current = instance;
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/reactflow-type") as FlowNodeType;
      if (!type || !rfInstance.current || !reactFlowWrapper.current) return;

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = rfInstance.current.screenToFlowPosition({
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      });

      const nodeId = getNextId(type);
      const newNode: Node = {
        id: nodeId,
        type,
        position,
        data: getDefaultData(type),
      };
      addNode(newNode, addAgent);
    },
    [addNode, addAgent]
  );

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNodeId(node.id);
    },
    [setSelectedNodeId]
  );

  const onNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (node.type === "agent") {
        setSelectedNodeId(node.id);
        // Auto-focus execute input — handled by ExecutePanel visibility
      }
    },
    [setSelectedNodeId]
  );

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, [setSelectedNodeId]);

  return (
    <div ref={reactFlowWrapper} className="h-full w-full">
      <ReactFlow
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onInit={onInit}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onPaneClick={onPaneClick}
        fitView
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls />
        <MiniMap
          nodeStrokeColor="#999"
          nodeColor={(n) => {
            if (n.type === "agent") return "#3b82f6";
            if (n.type === "tool") return "#22c55e";
            if (n.type === "prompt") return "#a855f7";
            return "#eee";
          }}
        />
      </ReactFlow>
    </div>
  );
}
