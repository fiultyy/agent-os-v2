"use client";

import { MemoryPanel } from "@/components/panels/MemoryPanel";
import { CommunicationPanel } from "@/components/panels/CommunicationPanel";
import { DebugPanel } from "@/components/panels/DebugPanel";
import { OrchestrationPanel } from "@/components/panels/OrchestrationPanel";
import { Header } from "@/components/layout/Header";
import { useState } from "react";
import { Brain, MessageSquare, Bug, Workflow, History } from "lucide-react";

type Tab = "memory" | "communication" | "debug" | "history" | "orchestration";

export default function MemoryPage() {
  const [activeTab, setActiveTab] = useState<Tab>("memory");

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "memory", label: "记忆", icon: <Brain className="h-4 w-4" /> },
    { id: "communication", label: "通信", icon: <MessageSquare className="h-4 w-4" /> },
    { id: "debug", label: "调试", icon: <Bug className="h-4 w-4" /> },
    { id: "history", label: "历史", icon: <History className="h-4 w-4" /> },
    { id: "orchestration", label: "编排", icon: <Workflow className="h-4 w-4" /> },
  ];

  return (
    <div className="flex h-screen flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        {/* Tab sidebar */}
        <div className="flex w-12 flex-col items-center gap-1 border-r bg-gray-50 py-3">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              title={tab.label}
              aria-label={tab.label}
              className={`rounded-lg p-2.5 transition-colors ${
                activeTab === tab.id
                  ? "bg-blue-100 text-blue-600"
                  : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
              }`}
            >
              {tab.icon}
            </button>
          ))}
        </div>

        {/* Panel content */}
        <div className="flex-1 overflow-hidden">
          {activeTab === "memory" && <MemoryPanel />}
          {activeTab === "communication" && <CommunicationPanel />}
          {activeTab === "debug" && <DebugPanel />}
          {activeTab === "history" && <DebugPanel />}
          {activeTab === "orchestration" && <OrchestrationPanel />}
        </div>
      </div>
    </div>
  );
}
