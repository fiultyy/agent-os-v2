// P1-1: Tick Canvas — React Flow wrapper
"use client";
import { useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { TickNode } from "./nodes/TickNode";
import { CommitteeVoteNode } from "./nodes/CommitteeVoteNode";
import { TabBar } from "./TabBar";
import { LODControl } from "./LODControl";
import { useCanvasStore } from "../../stores/canvasStore";
import { DEFAULT_LAYOUT } from "@/types/canvas";

export function TickCanvas() {
  const ticks = useCanvasStore(s => s.ticks);
  const events = useCanvasStore(s => s.events);
  const tabs = useCanvasStore(s => s.tabs);
  const activeTabId = useCanvasStore(s => s.activeTabId);
  const lod = useCanvasStore(s => s.lod);

  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = [];
    const es: Edge[] = [];
    const idxMap: Record<string, number> = {};

    ticks.forEach((tick, tickId) => {
      if (!idxMap[tick.branch_id]) idxMap[tick.branch_id] = 0;
      const idx = idxMap[tick.branch_id]++;
      ns.push({
        id: tickId,
        type: "tickNode",
        position: { x: DEFAULT_LAYOUT.tickX * idx, y: DEFAULT_LAYOUT.tickY },
        data: { tick, lod } as Record<string, unknown>,
      });
    });

    events.forEach(evt => {
      if (evt.type === "tool.call" && evt.tick_id) {
        const parentId = (evt.data.parent_tick_id as string) || "";
        if (parentId) {
          es.push({ id: parentId + "-" + evt.tick_id, source: parentId, target: evt.tick_id, type: "smoothstep", animated: true });
        }
      }
    });

    return { nodes: ns, edges: es };
  }, [ticks, events, lod]);

  const [flowNodes, , onNodesChange] = useNodesState(nodes);
  const [flowEdges, , onEdgesChange] = useEdgesState(edges);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodeTypes: any = { tickNode: TickNode, committeeVoteNode: CommitteeVoteNode };

  return (
    <div className="flex flex-col h-full">
      <TabBar />
      <LODControl />
      <div className="flex-1">
        <ReactFlow nodes={flowNodes} edges={flowEdges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes} fitView className="bg-slate-50">
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
      </div>
    </div>
  );
}
