"use client";

import { useDebugStore, type CommMessage } from "@/stores/debugStore";
import { MessageSquare, Send, Radio, ArrowRight } from "lucide-react";

const TYPE_ICONS: Record<string, React.ReactNode> = {
  task: <ArrowRight className="h-3 w-3 text-blue-500" />,
  result: <Send className="h-3 w-3 text-green-500" />,
  broadcast: <Radio className="h-3 w-3 text-orange-500" />,
  request: <MessageSquare className="h-3 w-3 text-purple-500" />,
  response: <Send className="h-3 w-3 text-teal-500" />,
  error: <span className="text-xs text-red-500">!</span>,
};

const PRIORITY_BADGE: Record<number, string> = {
  0: "bg-gray-100 text-gray-600",
  1: "bg-blue-100 text-blue-700",
  2: "bg-yellow-100 text-yellow-700",
  3: "bg-red-100 text-red-700",
};

export function CommunicationPanel() {
  const messages = useDebugStore((s) => s.messages);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <MessageSquare className="h-4 w-4 text-blue-600" />
          Agent 通信面板
        </div>
        <p className="mt-1 text-xs text-gray-500">
          实时显示 Agent 间的消息流
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            暂无通信消息
          </div>
        ) : (
          <div className="space-y-2">
            {messages.map((msg) => (
              <MessageCard key={msg.id} message={msg} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MessageCard({ message }: { message: CommMessage }) {
  const icon = TYPE_ICONS[message.messageType] ?? TYPE_ICONS.task;
  const priorityBadge = PRIORITY_BADGE[message.priority] ?? PRIORITY_BADGE[1];
  const isBroadcast = message.recipientId === null;

  return (
    <div className="rounded-lg border bg-white p-3 text-xs shadow-sm">
      <div className="flex items-center gap-2">
        {icon}
        <span className="font-mono text-[10px] text-gray-500">
          {message.senderId.slice(0, 8)}
        </span>
        <ArrowRight className="h-3 w-3 text-gray-300" />
        <span className="font-mono text-[10px] text-gray-500">
          {isBroadcast ? "ALL" : (message.recipientId?.slice(0, 8) ?? "?")}
        </span>
        <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium ${priorityBadge}`}>
          P{message.priority}
        </span>
      </div>
      <p className="mt-1.5 text-gray-700 line-clamp-2">{message.content}</p>
      <div className="mt-1.5 text-[10px] text-gray-400">
        {message.timestamp ? new Date(message.timestamp).toLocaleTimeString() : ""}
        {isBroadcast && (
          <span className="ml-2 rounded bg-orange-100 px-1 py-0.5 text-orange-700">
            broadcast
          </span>
        )}
        {message.correlationId && (
          <span className="ml-2 text-gray-400">
            corr: {message.correlationId.slice(0, 6)}
          </span>
        )}
      </div>
    </div>
  );
}
