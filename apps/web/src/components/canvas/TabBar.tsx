"use client";
import { useCanvasStore } from "../../stores/canvasStore";

export function TabBar() {
  const { tabs, activeTabId, addTab, removeTab, switchTab } = useCanvasStore();

  return (
    <div className="flex items-center gap-1 px-2 py-1 bg-gray-100 border-b border-gray-200 overflow-x-auto">
      {tabs.map(tab => (
        <div
          key={tab.tab_id}
          className={`flex items-center gap-1 px-3 py-1 rounded text-sm cursor-pointer transition-colors ${
            tab.tab_id === activeTabId
              ? "bg-white shadow-sm border border-gray-300 font-medium"
              : "bg-gray-50 hover:bg-gray-50 border border-transparent"
          }`}
          onClick={() => switchTab(tab.tab_id)}
        >
          <span className="text-gray-700">{tab.label}</span>
          {tabs.length > 1 && (
            <button
              className="ml-1 text-gray-400 hover:text-red-500 text-xs"
              onClick={(e) => { e.stopPropagation(); removeTab(tab.tab_id); }}
            >
              ×
            </button>
          )}
        </div>
      ))}
      <button
        onClick={() => addTab(tabs[0]?.branch_id || "main")}
        className="px-2 py-1 text-sm text-gray-500 hover:text-blue-600 border border-dashed border-gray-300 rounded"
      >
        + Tab
      </button>
    </div>
  );
}
