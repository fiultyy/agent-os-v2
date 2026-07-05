"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { DemoBanner } from "@/components/layout/DemoBanner";
import { FlowCanvas } from "@/components/canvas/FlowCanvas";
import { PropertyPanel } from "@/components/canvas/PropertyPanel";
import { ExecutePanel } from "@/components/canvas/ExecutePanel";
import { useFlowStore } from "@/stores/flowStore";

export default function CanvasPage() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);

  return (
    <div className="flex h-screen w-screen flex-col">
      <Header />
      <DemoBanner title="设计画布(演示原型)">
        拖拽节点暂不执行编排——仅 Agent 节点选中后可在底部执行栏单独运行一轮,
        Tool / Prompt 节点仅展示、连线无执行语义。
      </DemoBanner>
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
