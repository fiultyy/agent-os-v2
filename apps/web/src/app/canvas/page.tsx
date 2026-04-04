"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyPanel } from "@/components/canvas/PropertyPanel";
import { ExecutePanel } from "@/components/canvas/ExecutePanel";
import { useFlowStore } from "@/stores/flowStore";

export default function CanvasPage() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);

  return (
    <div className="flex h-screen w-screen flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <div className="flex flex-1 flex-col">
          <div className="flex-1">
            <FlowCanvas />
          </div>
          <ExecutePanel />
        </div>
        {selectedNodeId && <PropertyPanel />}
      </div>
    </div>
  );
}
