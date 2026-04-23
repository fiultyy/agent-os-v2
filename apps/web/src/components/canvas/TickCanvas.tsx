// P1-1: Tick Canvas — React Flow wrapper
"use client";
import { useMemo, useRef } from "react";
import { ReactFlow, Background, Controls, MiniMap, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { TickNode } from "./nodes/TickNode";
import { CommitteeVoteNode } from "./nodes/CommitteeVoteNode";
import { ToolNode } from "./nodes/ToolNode";
import { ToolResultNode } from "./nodes/ToolResultNode";
import { TabBar } from "./TabBar";
import { LODControl } from "./LODControl";
import { useCanvasStore } from "../../stores/canvasStore";
import { DEFAULT_LAYOUT } from "@/types/canvas";
import type { LODLevel, CanvasEvent, ToolCallInfo } from "@/types/canvas";

// ── W-2: Stable nodeTypes outside component body ──────────────
const nodeTypes = {
  tickNode: TickNode,
  committeeVoteNode: CommitteeVoteNode,
  toolNode: ToolNode,
  toolResultNode: ToolResultNode,
};

// ── Branch Y-offset config (C-3) ─────────────────────────────
const BRANCH_Y_OFFSET = 120;

export function TickCanvas() {
  const ticks = useCanvasStore(s => s.ticks);
  const events = useCanvasStore(s => s.events);
  const lod = useCanvasStore(s => s.lod);

  // ── C-3 + C-4 + W-9 + W-2: Unified node/edge computation ──
  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = [];
    const es: Edge[] = [];

    // ── C-3: Branch → Y offset mapping ──
    const branchDepthMap: Record<string, number> = {};
    let depthCounter = 0;
    // Ensure "main" always gets depth 0
    branchDepthMap["main"] = 0;
    depthCounter = 1;

    ticks.forEach((_tick, tickId) => {
      if (branchDepthMap[_tick.branch_id] === undefined) {
        branchDepthMap[_tick.branch_id] = depthCounter++;
      }
    });

    // ── Group ticks by branch for sequential edge building (C-4) ──
    const branchTickIds: Record<string, string[]> = {};
    const branchTickIdx: Record<string, Record<string, number>> = {};

    ticks.forEach((tick, tickId) => {
      if (!branchTickIds[tick.branch_id]) {
        branchTickIds[tick.branch_id] = [];
        branchTickIdx[tick.branch_id] = {};
      }
      const idx = branchTickIds[tick.branch_id].length;
      branchTickIds[tick.branch_id].push(tickId);
      branchTickIdx[tick.branch_id][tickId] = idx;
    });

    // ── Create Tick nodes ──
    ticks.forEach((tick, tickId) => {
      const depth = branchDepthMap[tick.branch_id] ?? 0;
      const idx = branchTickIdx[tick.branch_id]?.[tickId] ?? 0;
      ns.push({
        id: tickId,
        type: "tickNode",
        position: {
          x: DEFAULT_LAYOUT.tickX * idx,
          y: DEFAULT_LAYOUT.tickY + depth * BRANCH_Y_OFFSET,
        },
        data: { tick, lod } as Record<string, unknown>,
      });
    });

    // ── C-4: Sequential edges within each branch ──
    for (const branchId of Object.keys(branchTickIds)) {
      const ids = branchTickIds[branchId];
      for (let i = 1; i < ids.length; i++) {
        es.push({
          id: `seq-${ids[i - 1]}-${ids[i]}`,
          source: ids[i - 1],
          target: ids[i],
          type: "smoothstep",
          animated: false,
        });
      }
    }

    // ── C-4 + W-9: Tool nodes from tool.call events ──
    // Collect tool.call events grouped by tick
    const toolCallsByTick: Record<string, CanvasEvent[]> = {};
    events.forEach(evt => {
      if (evt.type === "tool.call" && evt.tick_id) {
        if (!toolCallsByTick[evt.tick_id]) toolCallsByTick[evt.tick_id] = [];
        toolCallsByTick[evt.tick_id].push(evt);
      }
    });

    // For each tick that has tool calls, create Tool nodes + ToolResult nodes
    const branchToolCount: Record<string, Record<string, number>> = {};
    for (const branchId of Object.keys(branchTickIds)) {
      branchToolCount[branchId] = {};
    }

    for (const tickId of Object.keys(toolCallsByTick)) {
      const tick = ticks.get(tickId);
      if (!tick) continue;

      const branchId = tick.branch_id;
      const tickDepth = branchDepthMap[branchId] ?? 0;
      const tickIdx = branchTickIdx[branchId]?.[tickId] ?? 0;
      const baseY = DEFAULT_LAYOUT.tickY + tickDepth * BRANCH_Y_OFFSET;
      const baseX = DEFAULT_LAYOUT.tickX * tickIdx;

      // Initialize tool counter for this tick within its branch
      if (!branchToolCount[branchId]) branchToolCount[branchId] = {};
      if (branchToolCount[branchId][tickId] === undefined) {
        branchToolCount[branchId][tickId] = 0;
      }

      // Collect tool.result events for status lookup
      const toolResults: Record<string, CanvasEvent> = {};
      events.forEach(evt => {
        if (evt.type === "tool.result" && evt.tick_id === tickId) {
          const cid = (evt.data.tool_call_id as string) || (evt.data.call_id as string) || "";
          if (cid) toolResults[cid] = evt;
        }
      });

      toolCallsByTick[tickId].forEach((tcEvt, tcIdx) => {
        const callId = (tcEvt.data.tool_call_id as string) || (tcEvt.data.call_id as string) || `tc-${tcIdx}`;
        const toolName = (tcEvt.data.tool_name as string) || "unknown";
        const toolNodeId = `tool-${tickId}-${callId}`;
        const resultNodeId = `result-${tickId}-${callId}`;

        // Determine status from tool.result event
        const resultEvt = toolResults[callId];
        const status = resultEvt?.data.error
          ? "error" as const
          : resultEvt
            ? "completed" as const
            : "running" as const;

        const resultText = resultEvt ? ((resultEvt.data.result as string) || "") : "";

        // ── Tool Node (W-9) ──
        const toolX = baseX + 60;
        const toolY = baseY + DEFAULT_LAYOUT.toolY + tcIdx * 60;
        ns.push({
          id: toolNodeId,
          type: "toolNode",
          position: { x: toolX, y: toolY },
          data: {
            label: toolName,
            lastStatus: status,
            duration: 0,
          } as Record<string, unknown>,
        });

        // ── Tool Result Node (W-9) ──
        ns.push({
          id: resultNodeId,
          type: "toolResultNode",
          position: { x: toolX + 200, y: toolY },
          data: {
            tool: {
              tool_call_id: callId,
              tool_name: toolName,
              label: toolName,
              summary: resultText.slice(0, 100),
              args: JSON.stringify(tcEvt.data.arguments ?? {}),
              result: resultText,
              status,
            } satisfies ToolCallInfo,
            lod,
          } as Record<string, unknown>,
        });

        // ── Edge: Tick → Tool (C-4) ──
        es.push({
          id: `tick-tool-${tickId}-${callId}`,
          source: tickId,
          target: toolNodeId,
          type: "smoothstep",
          animated: true,
        });

        // ── Edge: Tool → ToolResult (C-4) ──
        es.push({
          id: `tool-result-${tickId}-${callId}`,
          source: toolNodeId,
          target: resultNodeId,
          type: "smoothstep",
          animated: false,
        });
      });
    }

    return { nodes: ns, edges: es };
  }, [ticks, events, lod]);

  // ── W-2: Controlled mode — no useNodesState/useEdgesState ──
  return (
    <div className="flex flex-col h-full">
      <TabBar />
      <LODControl />
      <div className="flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          className="bg-slate-50"
        >
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
      </div>
    </div>
  );
}
