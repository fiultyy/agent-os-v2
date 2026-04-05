"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { Bot, Send, Loader2, Layout, MessageSquare } from "lucide-react";
import { executeWithSSE } from "@/lib/api";

interface Agent {
  id: string;
  name: string;
  description: string;
  model: string;
  status: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export default function Home() {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then((agents: Agent[]) => {
        if (agents.length > 0) setAgent(agents[0]);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || loading || !agent) return;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    // Track intermediate output from SSE events
    let assistantContent = "";

    try {
      await executeWithSSE(
        agent.id,
        text,
        sessionId || undefined,
        (event) => {
          if (event.event === "node_start") {
            // Could show "thinking..." indicator for specific nodes
            const node = event.data.node as string;
            if (node === "llm" || node === "llm_synthesize") {
              // AI is generating response
            } else if (node === "tool") {
              // Tool is executing
            }
          } else if (event.event === "node_complete") {
            const node = event.data.node as string;
            const output = event.data.output as string;
            if (node === "llm" || node === "llm_synthesize") {
              assistantContent = output || "";
            }
          } else if (event.event === "execution_complete") {
            const finalOutput = (event.data.output as string) || assistantContent;
            if (!sessionId && event.data.session_id) {
              setSessionId(event.data.session_id as string);
            }
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: finalOutput },
            ]);
          } else if (event.event === "error") {
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: `[Error] ${event.data.message}` },
            ]);
          }
        }
      );
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `[Error] ${err instanceof Error ? err.message : "Unknown error"}` },
      ]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, agent, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r bg-white">
        <div className="border-b p-4">
          <h1 className="flex items-center gap-2 text-lg font-bold">
            <Bot className="h-5 w-5 text-blue-600" />
            Agent OS
          </h1>
        </div>

        {/* Agent card */}
        <div className="border-b p-4">
          <div className="rounded-lg bg-blue-50 p-3">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-sm text-white">
                AI
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {agent?.name ?? "Loading..."}
                </div>
                <div className="truncate text-xs text-gray-500">
                  {agent?.model ?? "—"}
                </div>
              </div>
            </div>
            <div className="mt-2 flex items-center gap-1">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  agent?.status === "idle" ? "bg-green-500" : "bg-gray-300"
                }`}
              />
              <span className="text-xs text-gray-500">
                {agent?.status === "idle" ? "Online" : agent ? "Busy" : "—"}
              </span>
            </div>
          </div>
        </div>

        {/* Nav links */}
        <nav className="flex-1 space-y-1 p-3">
          <Link
            href="/canvas"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-100"
          >
            <Layout className="h-4 w-4" />
            流程画布
          </Link>
          <Link
            href="/agents"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-100"
          >
            <Bot className="h-4 w-4" />
            Agent 管理
          </Link>
        </nav>

        <div className="border-t p-3">
          <p className="text-center text-xs text-gray-400">Agent OS v0.1</p>
        </div>
      </aside>

      {/* Chat area */}
      <main className="flex flex-1 flex-col">
        {/* Header */}
        <header className="flex items-center gap-2 border-b bg-white px-6 py-3">
          <MessageSquare className="h-5 w-5 text-blue-600" />
          <span className="font-medium">对话</span>
          {agent && (
            <span className="text-sm text-gray-400">— {agent.name}</span>
          )}
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center text-gray-400">
              <Bot className="mb-4 h-12 w-12" />
              <p className="text-lg font-medium">开始对话</p>
              <p className="mt-1 text-sm">发送一条消息开始和 AI 助手聊天</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`mb-4 flex ${
                msg.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              <div
                className={`max-w-[70%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-gray-800 shadow-sm"
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="mb-4 flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl bg-white px-4 py-2.5 shadow-sm">
                <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                <span className="text-sm text-gray-400">思考中...</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t bg-white px-6 py-4">
          <div className="flex items-end gap-3">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              rows={1}
              className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-2.5 text-sm outline-none transition-colors focus:border-blue-400"
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
