// P1-4: LOD Control — Level of Detail switcher
"use client";

import { useCanvasStore } from "../../stores/canvasStore";
import type { LODLevel } from "@/types/canvas";

const LOD_OPTIONS: { level: LODLevel; label: string; desc: string }[] = [
  { level: 1, label: "L1", desc: "Tool name only" },
  { level: 2, label: "L2", desc: "Summary + tools" },
  { level: 3, label: "L3", desc: "Full dev-debug" },
];

export function LODControl() {
  // Note: LOD is currently stored per-tab in viewport; for simplicity using global here
  // In production this would come from active tab's viewport
  const activeTabId = useCanvasStore(s => s.activeTabId);
  const lod = useCanvasStore(s => s.lod);
  const setLOD = useCanvasStore(s => s.setLOD);

  if (!activeTabId) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white border-b border-gray-200">
      <span className="text-xs text-gray-500">LOD:</span>
      <div className="flex gap-1">
        {LOD_OPTIONS.map(({ level, label, desc }) => (
          <button
            key={level}
            title={desc}
            className={`px-2 py-0.5 text-xs rounded transition-colors ${
              lod === level
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`
            }
            onClick={() => setLOD(level)}
          >
            {label}
          </button>
        ))}
      </div>
      <span className="text-xs text-gray-400">Summary + tools</span>
    </div>
  );
}
