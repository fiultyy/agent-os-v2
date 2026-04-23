"use client";

import { useState, useCallback } from "react";
import { GitBranch, GitMerge, Trash2, Send, Plus, Check, Circle, AlertCircle } from "lucide-react";
import { useCanvasStore } from "../../stores/canvasStore";
import { canvasWsClient } from "../../lib/canvas/wsClient";
import type { Branch } from "@/types/canvas";

// ── Branch status badge ──────────────────────────────────────

type BranchStatus = "active" | "merged" | "pruned";

function StatusBadge({ status }: { status: BranchStatus }) {
  if (status === "active") return <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full bg-green-100 text-green-700"><Check className="h-3 w-3" />Active</span>;
  if (status === "merged") return <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full bg-blue-100 text-blue-700"><GitMerge className="h-3 w-3" />Merged</span>;
  return <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full bg-gray-100 text-gray-500"><AlertCircle className="h-3 w-3" />Pruned</span>;
}

// ── Branch row ───────────────────────────────────────────────

function BranchRow({ branch, isActive, onSwitch, onMerge, onPrune }: {
  branch: Branch;
  isActive: boolean;
  onSwitch: () => void;
  onMerge: () => void;
  onPrune: () => void;
}) {
  const status = (branch.status as BranchStatus) || "active";
  return (
    <div
      className={`flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
        isActive ? "bg-blue-50 border-blue-300 shadow-sm" : "bg-white border-gray-200 hover:bg-gray-50"
      }`}
      onClick={onSwitch}
    >
      <GitBranch className={`h-4 w-4 flex-shrink-0 ${isActive ? "text-blue-600" : "text-gray-400"}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-800 truncate">{branch.branch_id.slice(0, 12)}</span>
          {isActive && <Circle className="h-2 w-2 fill-blue-500 text-blue-500 flex-shrink-0" />}
        </div>
        <div className="text-[10px] text-gray-400 mt-0.5">
          {branch.branch_id.slice(0, 8)}… · {new Date(branch.created_at).toLocaleTimeString()}
        </div>
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        {isActive && (
          <>
            <button
              title="Merge to main"
              onClick={(e) => { e.stopPropagation(); onMerge(); }}
              className="p-1 rounded hover:bg-green-100 text-gray-400 hover:text-green-600 transition-colors"
            >
              <GitMerge className="h-3.5 w-3.5" />
            </button>
            <button
              title="Prune branch"
              onClick={(e) => { e.stopPropagation(); onPrune(); }}
              className="p-1 rounded hover:bg-red-100 text-gray-400 hover:text-red-600 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Cross-tab send dialog ────────────────────────────────────

function CrossTabSend({ branchId, onClose }: { branchId: string; onClose: () => void }) {
  const { tabs, addTab } = useCanvasStore();
  const [selectedTabId, setSelectedTabId] = useState<string | null>(null);

  const handleSend = useCallback(() => {
    if (!selectedTabId) return;
    const tab = tabs.find(t => t.tab_id === selectedTabId);
    if (tab) {
      // Open a new tab pointing to this branch in the target tab's session context
      addTab(branchId, `Branch → Tab`);
    }
    onClose();
  }, [selectedTabId, tabs, branchId, addTab, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-4 w-72" onClick={e => e.stopPropagation()}>
        <div className="text-sm font-semibold text-gray-800 mb-3">Send to Tab</div>
        <div className="space-y-1 max-h-40 overflow-y-auto">
          {tabs.length === 0 ? (
            <div className="text-xs text-gray-400 text-center py-2">No tabs open</div>
          ) : (
            tabs.map(tab => (
              <button
                key={tab.tab_id}
                onClick={() => setSelectedTabId(tab.tab_id)}
                className={`w-full text-left px-3 py-1.5 rounded text-xs transition-colors ${
                  selectedTabId === tab.tab_id
                    ? "bg-blue-100 text-blue-700 font-medium"
                    : "hover:bg-gray-100 text-gray-600"
                }`}
              >
                {tab.label}
                <span className="text-gray-400 ml-1">({tab.tab_id.slice(0, 8)}…)</span>
              </button>
            ))
          )}
        </div>
        <div className="flex gap-2 mt-3">
          <button onClick={onClose} className="flex-1 py-1.5 text-xs text-gray-600 border border-gray-200 rounded hover:bg-gray-50">
            Cancel
          </button>
          <button
            onClick={handleSend}
            disabled={!selectedTabId}
            className="flex-1 py-1.5 text-xs text-white bg-blue-600 rounded disabled:opacity-40 hover:bg-blue-700"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

// ── BranchManager ────────────────────────────────────────────

export function BranchManager() {
  const { branches, addBranch, tabs, addTab } = useCanvasStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [crossTabBranchId, setCrossTabBranchId] = useState<string | null>(null);

  const activeBranchId = tabs.find(t => t.tab_id === useCanvasStore.getState().activeTabId)?.branch_id ?? null;

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return;
    const sessionId = useCanvasStore.getState().sessionId || "";
    const parentBranchId = activeBranchId || "main";
    try {
      const res = await fetch("/api/canvas/branch/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, parent_branch_id: parentBranchId, fork_tick_id: null }),
      });
      if (!res.ok) { console.error("[Branch] Create failed:", res.status); return; }
      const branchData = await res.json();
      addBranch(branchData);
      addTab(branchData.branch_id, branchData.branch_id.slice(0, 12));
    } catch (err) {
      console.error("[Branch] Create error:", err);
    }
    setNewName("");
    setShowCreate(false);
  }, [newName, addBranch, addTab, activeBranchId]);

  const handleMerge = useCallback((branchId: string) => {
    const sessionId = useCanvasStore.getState().sessionId || "";
    fetch("/api/canvas/branch/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, branch_id: branchId, target_branch_id: "main" }),
    })
      .then(res => { if (!res.ok) console.error("[Branch] Merge failed:", res.status); })
      .catch(err => console.error("[Branch] Merge error:", err));
  }, []);

  const handlePrune = useCallback((branchId: string) => {
    const sessionId = useCanvasStore.getState().sessionId || "";
    fetch("/api/canvas/branch/prune", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, branch_id: branchId }),
    })
      .then(res => { if (!res.ok) console.error("[Branch] Prune failed:", res.status); })
      .catch(err => console.error("[Branch] Prune error:", err));
  }, []);

  return (
    <div className="flex flex-col h-full bg-slate-50 border-r border-slate-200 w-60">
      <div className="px-3 py-2 border-b border-slate-200">
        <div className="flex items-center justify-between">
          <div className="text-xs font-semibold text-slate-600 uppercase">Branches</div>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="p-1 rounded hover:bg-blue-100 text-gray-400 hover:text-blue-600 transition-colors"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="px-3 py-2 border-b border-slate-200 bg-white">
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleCreate()}
            placeholder="Branch name..."
            autoFocus
            className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-blue-400"
          />
          <div className="flex gap-1 mt-1.5">
            <button onClick={() => { setShowCreate(false); setNewName(""); }}
              className="flex-1 py-1 text-[10px] text-gray-500 border border-gray-200 rounded hover:bg-gray-50">
              Cancel
            </button>
            <button onClick={handleCreate} disabled={!newName.trim()}
              className="flex-1 py-1 text-[10px] text-white bg-blue-600 rounded disabled:opacity-40">
              Create
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {branches.length === 0 ? (
          <div className="text-xs text-gray-400 text-center py-6">No branches yet</div>
        ) : (
          branches.map(branch => {
            const isActive = branch.branch_id === activeBranchId;
            return (
              <div key={branch.branch_id}>
                <BranchRow
                  branch={branch}
                  isActive={isActive}
                  onSwitch={() => {
                    addTab(branch.branch_id, branch.branch_id.slice(0, 12));
                    canvasWsClient.send({ cmd: "switch_branch", branch_id: branch.branch_id });
                  }}
                  onMerge={() => handleMerge(branch.branch_id)}
                  onPrune={() => handlePrune(branch.branch_id)}
                />
                {isActive && tabs.length > 1 && (
                  <button
                    onClick={() => setCrossTabBranchId(branch.branch_id)}
                    className="mt-1 ml-6 flex items-center gap-1 px-2 py-0.5 text-[10px] text-gray-400 hover:text-blue-600 rounded hover:bg-blue-50 transition-colors"
                  >
                    <Send className="h-3 w-3" />
                    Send to another tab
                  </button>
                )}
              <div className="ml-6 mt-0.5">
                <StatusBadge status={(branch.status as BranchStatus) || "active"} />
              </div>
              </div>
            );
          })
        )}
      </div>

      {crossTabBranchId && (
        <CrossTabSend branchId={crossTabBranchId} onClose={() => setCrossTabBranchId(null)} />
      )}
    </div>
  );
}
