"use client";

import { useState, KeyboardEvent } from "react";
import { Send } from "lucide-react";

interface ChatInterfaceProps {
  onSendMessage: (message: string) => void;
  disabled?: boolean;
}

export function ChatInterface({ onSendMessage, disabled }: ChatInterfaceProps) {
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);

  const handleSend = async () => {
    if (!input.trim() || disabled || isSending) {
      return;
    }

    setIsSending(true);
    try {
      await onSendMessage(input);
      setInput("");
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  return (
    <div className="p-4">
      <div className="rounded-lg border bg-white p-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            disabled
              ? "请先选择 harness 和 session"
              : "输入消息... (Enter 发送, Shift+Enter 换行)"
          }
          disabled={disabled || isSending}
          rows={2}
          className="w-full resize-none rounded border-none bg-transparent text-sm outline-none placeholder:text-gray-400 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <div className="mt-2 flex items-center justify-between">
          <div className="text-xs text-gray-400">
            {isSending ? "发送中..." : `${input.length} 字符`}
          </div>
          <button
            onClick={handleSend}
            disabled={!input.trim() || disabled || isSending}
            className="flex items-center gap-1.5 rounded bg-blue-500 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
