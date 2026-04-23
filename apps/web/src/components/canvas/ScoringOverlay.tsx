"use client";

import { useMemo } from "react";
import { useCanvasStore } from "../../stores/canvasStore";
import type { CanvasEvent, LODLevel } from "@/types/canvas";

// ── Scoring signal extraction ────────────────────────────────

interface ScoringEdge {
  id: string;
  source: string;
  target: string;
  weight: number;
  label: string;
}

interface ScoringNode {
  id: string;
  x: number;
  y: number;
  signal: number;
  label: string;
}

/** Extract scoring.signal events and derive node/edge data */
function extractScoringData(events: CanvasEvent[]): { nodes: ScoringNode[]; edges: ScoringEdge[] } {
  const scoringEvents = events.filter(e => e.type === "scoring.signal");
  if (scoringEvents.length === 0) return { nodes: [], edges: [] };

  const nodeMap = new Map<string, ScoringNode>();
  const edgeList: ScoringEdge[] = [];

  // Position helpers — pack nodes in a grid-like layout
  const cols = 6;
  let idx = 0;
  const offsetX = 20;
  const offsetY = 20;
  const gapX = 90;
  const gapY = 70;

  for (const evt of scoringEvents) {
    const d = evt.data;
    const forward = (d.forward as number) ?? 0;
    const backward = (d.backward as number) ?? 0;
    const co_occurrence = (d.co_occurrence as number) ?? 0;
    const sourceId = (d.source_node as string) || `node_${evt.event_id}`;
    const targetId = (d.target_node as string) || "";
    const signal = Math.max(forward, backward, co_occurrence);

    // Source node
    if (!nodeMap.has(sourceId)) {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      nodeMap.set(sourceId, {
        id: sourceId,
        x: offsetX + col * gapX,
        y: offsetY + row * gapY,
        signal,
        label: (d.source_label as string) || sourceId.slice(0, 12),
      });
      idx++;
    }

    // Target node
    if (targetId && !nodeMap.has(targetId)) {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      nodeMap.set(targetId, {
        id: targetId,
        x: offsetX + col * gapX,
        y: offsetY + row * gapY,
        signal,
        label: (d.target_label as string) || targetId.slice(0, 12),
      });
      idx++;
    }

    // Edge
    if (targetId) {
      edgeList.push({
        id: `edge_${sourceId}_${targetId}`,
        source: sourceId,
        target: targetId,
        weight: signal,
        label: `f:${forward.toFixed(2)} b:${backward.toFixed(2)} c:${co_occurrence.toFixed(2)}`,
      });
    }
  }

  return { nodes: Array.from(nodeMap.values()), edges: edgeList };
}

// ── Color helpers ────────────────────────────────────────────

function signalColor(signal: number): string {
  if (signal > 0.7) return "rgba(239, 68, 68, 0.7)";   // red
  if (signal > 0.4) return "rgba(251, 191, 36, 0.6)";   // amber
  return "rgba(96, 165, 250, 0.5)";                     // blue
}

function signalBorderColor(signal: number): string {
  if (signal > 0.7) return "#ef4444";
  if (signal > 0.4) return "#f59e0b";
  return "#60a5fa";
}

function edgeColor(weight: number): string {
  if (weight > 0.7) return "rgba(239, 68, 68, 0.5)";
  if (weight > 0.4) return "rgba(251, 191, 36, 0.4)";
  return "rgba(148, 163, 184, 0.3)";
}

// ── SVG Overlay component ────────────────────────────────────

export function ScoringOverlay() {
  const events = useCanvasStore(s => s.events);
  const lod = useCanvasStore(s => s.lod);

  // Don't render at L1 — too noisy
  if (lod === 1) return null;

  const { nodes, edges } = useMemo(() => extractScoringData(events), [events]);

  if (nodes.length === 0) return null;

  // Calculate bounds
  const maxX = Math.max(...nodes.map(n => n.x)) + 80;
  const maxY = Math.max(...nodes.map(n => n.y)) + 50;
  const nodeLookup = new Map(nodes.map(n => [n.id, n]));

  return (
    <div className="absolute inset-0 pointer-events-none z-10" style={{ opacity: 0.85 }}>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${maxX} ${maxY}`}
        className="select-none"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Edges */}
        {edges.map(edge => {
          const src = nodeLookup.get(edge.source);
          const tgt = nodeLookup.get(edge.target);
          if (!src || !tgt) return null;
          const mx = (src.x + tgt.x) / 2;
          const my = (src.y + tgt.y) / 2;
          return (
            <g key={edge.id}>
              <line
                x1={src.x + 30} y1={src.y + 15}
                x2={tgt.x + 30} y2={tgt.y + 15}
                stroke={edgeColor(edge.weight)}
                strokeWidth={Math.max(1, edge.weight * 3)}
              />
              {/* Tooltip on hover — rendered as small label for L3 */}
              {lod === 3 && (
                <text x={mx} y={my - 4} textAnchor="middle" className="text-[8px] fill-gray-500 pointer-events-none">
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}

        {/* Nodes (heat circles) */}
        {nodes.map(node => {
          const r = 12 + node.signal * 14;
          return (
            <g key={node.id}>
              <circle
                cx={node.x + 30}
                cy={node.y + 15}
                r={r}
                fill={signalColor(node.signal)}
                stroke={signalBorderColor(node.signal)}
                strokeWidth={1.5}
              />
              {/* Node label — show at L2 and above */}
              {(lod === 2 || lod === 3) && (
                <text
                  x={node.x + 30}
                  y={node.y + 15 + r + 10}
                  textAnchor="middle"
                  className="text-[9px] fill-gray-600 pointer-events-none"
                >
                  {node.label}
                </text>
              )}
              {/* Signal value — show at L3 */}
              {lod === 3 && (
                <text
                  x={node.x + 30}
                  y={node.y + 15 + 4}
                  textAnchor="middle"
                  className="text-[8px] fill-white font-bold pointer-events-none"
                >
                  {node.signal.toFixed(2)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
