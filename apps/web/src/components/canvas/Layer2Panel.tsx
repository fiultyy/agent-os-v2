// P2-2: Layer 2 Staging Panel
"use client";
import { useState } from "react";
import { useLayer2Store } from "../../stores/layer2Store";
import { useCanvasStore } from "../../stores/canvasStore";
import { canvasWsClient } from "../../lib/canvas/wsClient";
import type { Layer2NodeType } from "@/types/canvas";

export function Layer2Panel() {
  const { staging, addNode, removeNode, clear, submitPayload } = useLayer2Store();
  const { tabs, activeTabId } = useCanvasStore();
  const [inputText, setInputText] = useState("");
  const [inputType, setInputType] = useState<Layer2NodeType>("text");
  const activeTab = tabs.find(t => t.tab_id === activeTabId);

  const handleAdd = () => {
    if (!inputText.trim()) return;
    addNode(inputType, inputText.trim());
    setInputText("");
  };

  const handleSubmit = () => {
    if (!activeTab || staging.length === 0) return;
    const payload = submitPayload(activeTab.session_id, activeTab.branch_id);
    console.log("[Layer2] Submit:", payload);
    canvasWsClient.send(JSON.stringify(payload));
    clear();
  };

  return (
    <div className="flex flex-col h-full bg-slate-50 border-l border-slate-200 w-64">
      <div className="px-3 py-2 border-b border-slate-200">
        <div className="text-xs font-semibold text-slate-600 uppercase">Layer 2 — Staging</div>
        <div className="text-xs text-slate-400 mt-0.5">Pre-build next Tick</div>
      </div>
      <div className="p-2 space-y-1.5">
        <div className="flex gap-1">
          <button onClick={() => setInputType("text")}
            className={"px-2 py-0.5 text-xs rounded " + (inputType === "text" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-500")}>
            Text
          </button>
          <button onClick={() => setInputType("command")}
            className={"px-2 py-0.5 text-xs rounded " + (inputType === "command" ? "bg-purple-100 text-purple-700" : "bg-gray-100 text-gray-500")}>
            /Cmd
          </button>
        </div>
        <textarea value={inputText} onChange={e => setInputText(e.target.value)}
          placeholder="Type..." rows={2}
          className="w-full px-2 py-1.5 text-xs border border-gray-200 rounded resize-none" />
        <button onClick={handleAdd} disabled={!inputText.trim()}
          className="w-full py-1 text-xs bg-blue-600 text-white rounded disabled:opacity-40">
          Add
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {staging.length === 0 ? (
          <div className="text-xs text-gray-400 text-center py-4">Empty</div>
        ) : (
          [...staging].sort((a, b) => a.order - b.order).map((n, i) => (
            <div key={n.id} className={"flex gap-1 p-1 rounded text-xs mb-1 " + (n.type === "command" ? "bg-purple-50" : "bg-white")}>
              <span className="text-gray-400">{i + 1}.</span>
              <span className="flex-1 truncate font-mono">{n.content}</span>
              <button onClick={() => removeNode(n.id)} className="text-gray-400 hover:text-red-500">x</button>
            </div>
          ))
        )}
      </div>
      <div className="p-2 border-t">
        <button onClick={handleSubmit} disabled={staging.length === 0}
          className="w-full py-2 text-sm bg-green-600 text-white rounded font-medium disabled:opacity-40">
          Submit ({staging.length})
        </button>
      </div>
    </div>
  );
}
