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
import { createAgent } from "@/lib/api";
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
      addNode(newNode);

      // Sync to agent store + backend API if it's an agent node
      if (type === "agent") {
        const label = String(newNode.data.label);
        createAgent({ name: label, model: "gpt-4o-mini" })
          .then((remote) => {
            updateNodeData(nodeId, { agentId: remote.id, label: remote.name });
            addAgent({
              id: remote.id,
              name: remote.name,
              description: remote.description ?? "",
              status: remote.status ?? "idle",
              model: remote.model ?? "gpt-4o-mini",
              tools: remote.tools ?? [],
              createdAt: remote.createdAt ?? new Date().toISOString(),
              updatedAt: remote.updatedAt ?? new Date().toISOString(),
            });
          })
          .catch(() => {
            // Fallback: still add locally even if API fails
            addAgent({
              id: nodeId,
              name: label,
              description: "",
              status: "idle",
              model: "gpt-4o-mini",
              tools: [],
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            });
          });
      }
    },
    [addNode, addAgent, updateNodeData]
  );

  const onNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNodeId(node.id);
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
