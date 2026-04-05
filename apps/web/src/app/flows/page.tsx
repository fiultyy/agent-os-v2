"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyPanel } from "@/components/canvas/PropertyPanel";
import { ExecutePanel } from "@/components/canvas/ExecutePanel";
import { useFlowStore } from "@/stores/flowStore";
import { useAgentStore } from "@/stores/agentStore";
import { getAgents } from "@/lib/api";
import { Bot, Loader2 } from "lucide-react";
import type { FlowNodeType } from "@/types/flow";

export default function FlowsPage() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const agents = useAgentStore((s) => s.agents);
  const addAgent = useAgentStore((s) => s.addAgent);
  const [loading, setLoading] = useState(true);

  // Fetch agents on mount and populate the store
  useEffect(() => {
    async function load() {
      try {
        const list = await getAgents();
        for (const a of list) {
          if (!agents.find((x) => x.id === a.id)) {
            addAgent(a);
          }
        }
      } catch {
        // Agent list fetch failure is non-fatal for the flows page
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function onDragStart(e: React.DragEvent, agentId: string, agentName: string) {
    e.dataTransfer.setData("application/reactflow-type", "agent");
    e.dataTransfer.setData("application/reactflow-agent-id", agentId);
    e.dataTransfer.setData("application/reactflow-agent-name", agentName);
    e.dataTransfer.effectAllowed = "move";
  }

  return (
    <div className="flex h-screen w-screen flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Component palette + Agent list for drag */}
        <aside className="flex h-full w-56 flex-col border-r bg-gray-50">
          <Sidebar />
          {/* Agent list for drag-to-canvas */}
          <div className="flex-1 overflow-y-auto border-t px-3 py-2">
            <p className="mb-2 text-xs text-gray-400">拖拽 Agent 到画布</p>
            {loading ? (
              <div className="flex items-center justify-center py-4 text-gray-400">
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                <span className="text-xs">加载 Agent...</span>
              </div>
            ) : agents.length === 0 ? (
              <div className="py-4 text-center text-xs text-gray-400">
                暂无 Agent，请先
                <a href="/agents" className="text-blue-500 hover:underline">创建</a>
              </div>
            ) : (
              <div className="space-y-1">
                {agents.map((agent) => (
                  <div
                    key={agent.id}
                    draggable
                    onDragStart={(e) => onDragStart(e, agent.id, agent.name)}
                    className="flex cursor-grab items-center gap-2 rounded border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-sm text-blue-700 transition-shadow hover:shadow-md active:cursor-grabbing"
                  >
                    <Bot className="h-3.5 w-3.5" />
                    <span className="truncate font-medium">{agent.name}</span>
                    <span className={`ml-auto h-2 w-2 rounded-full ${
                      agent.status === "running" ? "bg-green-500" :
                      agent.status === "error" ? "bg-red-500" : "bg-gray-300"
                    }`} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* Center: Canvas + Execute */}
        <div className="flex flex-1 flex-col">
          <div className="flex-1">
            <FlowCanvas />
          </div>
          <ExecutePanel />
        </div>

        {/* Right: Property panel (conditional) */}
        {selectedNodeId && <PropertyPanel />}
      </div>
    </div>
  );
}
