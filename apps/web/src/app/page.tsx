"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import {
  Bot,
  Send,
  Loader2,
  MessageSquare,
  Plus,
  Trash2,
  ChevronDown,
  Activity,
} from "lucide-react";
import {
  executeWithSSE,
  getConversationMessages,
  deleteConversation,
  type ConversationItem,
} from "@/lib/api";
import { dispatchSSEEvent } from "@/lib/sse-dispatch";
import { useDebugStore } from "@/stores/debugStore";
import { useConversationStore } from "@/stores/conversationStore";
import { Header } from "@/components/layout/Header";

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
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [showAgentMenu, setShowAgentMenu] = useState(false);

  const conversations = useConversationStore((s) => s.conversations);
  const activeConversationId = useConversationStore((s) => s.activeConversationId);
  const setActiveConversation = useConversationStore((s) => s.setActive);
  const loadConversations = useConversationStore((s) => s.loadConversations);
  const removeConversation = useConversationStore((s) => s.removeConversation);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const addMemoryEvent = useDebugStore((s) => s.addMemoryEvent);
  const addMessage = useDebugStore((s) => s.addMessage);
  const addObservationEvent = useDebugStore((s) => s.addObservationEvent);

  // 拉 agents + conversations(首次 mount)
  useEffect(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then((list: Agent[]) => {
        setAgents(list);
        if (list.length > 0) setAgent((prev) => prev ?? list[0]);
      })
      .catch(() => {});
    loadConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 选中一个对话:拉历史消息 + 切到该对话的 agent
  const selectConversation = useCallback(
    async (conv: ConversationItem) => {
      setActiveConversation(conv.id);
      setSessionId(conv.id);
      const convAgent = agents.find((a) => a.id === conv.agentId);
      if (convAgent) setAgent(convAgent);
      try {
        const msgs = await getConversationMessages(conv.id);
        setMessages(msgs.map((m) => ({ role: m.role, content: m.content })));
      } catch {
        setMessages([]);
      }
    },
    [agents, setActiveConversation]
  );

  const newConversation = useCallback(() => {
    setActiveConversation(null);
    setSessionId("");
    setMessages([]);
  }, [setActiveConversation]);

  const handleDeleteConversation = useCallback(
    async (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      try {
        await deleteConversation(id);
        removeConversation(id);
        if (activeConversationId === id) newConversation();
      } catch {
        // 静默
      }
    },
    [activeConversationId, removeConversation, newConversation]
  );

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || loading || !agent) return;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    let assistantContent = "";
    try {
      await executeWithSSE(agent.id, text, sessionId || undefined, (event) => {
        dispatchSSEEvent(event, { addMemoryEvent, addMessage, addObservationEvent });
        if (event.event === "node_complete") {
          const node = event.data.node as string;
          if (node === "llm" || node === "llm_synthesize") {
            assistantContent = (event.data.output as string) || "";
          }
        } else if (event.event === "execution_complete") {
          const finalOutput = (event.data.output as string) || assistantContent;
          const returnedSid = (event.data.session_id as string) || "";
          // 新对话(sessionId 空)首次拿到后端生成的 session_id → 锁定到该对话
          if (!sessionId && returnedSid) {
            setSessionId(returnedSid);
            setActiveConversation(returnedSid);
          }
          setMessages((prev) => [...prev, { role: "assistant", content: finalOutput }]);
          // 刷新左侧:新对话首次入库后出现,或已有对话 updated_at 前移
          loadConversations();
        } else if (event.event === "error") {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: `[Error] ${event.data.message}` },
          ]);
        }
      });
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `[Error] ${err instanceof Error ? err.message : "Unknown error"}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [
    input,
    loading,
    agent,
    sessionId,
    setActiveConversation,
    loadConversations,
    addMemoryEvent,
    addMessage,
    addObservationEvent,
  ]);

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
    <div className="flex h-screen flex-col bg-gray-50">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        {/* 左侧栏:对话列表 */}
        <aside className="flex w-64 shrink-0 flex-col border-r bg-white">
          <div className="border-b p-3">
            <button
              onClick={newConversation}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              <Plus className="h-4 w-4" /> 新建对话
            </button>
            {/* agent 选择器(决定新建对话用哪个 agent) */}
            <div className="relative mt-2">
              <button
                onClick={() => setShowAgentMenu((v) => !v)}
                className="flex w-full items-center justify-between rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
                aria-label="选择 agent"
              >
                <span className="flex items-center gap-1.5">
                  <Bot className="h-3 w-3" />
                  {agent?.name ?? "选 Agent"}
                </span>
                <ChevronDown className="h-3 w-3" />
              </button>
              {showAgentMenu && (
                <div className="absolute z-10 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg">
                  {agents.length === 0 && (
                    <div className="px-3 py-2 text-xs text-gray-400">无可用 agent</div>
                  )}
                  {agents.map((a) => (
                    <button
                      key={a.id}
                      onClick={() => {
                        setAgent(a);
                        setShowAgentMenu(false);
                      }}
                      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-gray-50 ${
                        agent?.id === a.id ? "text-blue-600" : "text-gray-700"
                      }`}
                    >
                      <Bot className="h-3 w-3" />
                      <span className="flex-1 truncate">{a.name}</span>
                      <span className="text-[10px] text-gray-400">{a.model}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {conversations.length === 0 ? (
              <div className="py-8 text-center text-xs text-gray-400">暂无对话</div>
            ) : (
              conversations.map((c) => (
                <div
                  key={c.id}
                  onClick={() => selectConversation(c)}
                  className={`group mb-1 flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                    activeConversationId === c.id
                      ? "bg-blue-50 text-blue-700"
                      : "text-gray-700 hover:bg-gray-100"
                  }`}
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-60" />
                  <span className="flex-1 truncate">{c.title || "(新对话)"}</span>
                  <button
                    onClick={(e) => handleDeleteConversation(c.id, e)}
                    className="opacity-0 transition-opacity group-hover:opacity-100"
                    aria-label="删除对话"
                  >
                    <Trash2 className="h-3 w-3 text-gray-400 hover:text-red-500" />
                  </button>
                </div>
              ))
            )}
          </div>
        </aside>

        {/* 右侧:聊天主区 */}
        <main className="flex flex-1 flex-col">
          <header className="flex items-center gap-2 border-b bg-white px-6 py-3">
            <MessageSquare className="h-5 w-5 text-blue-600" />
            <span className="font-medium">对话</span>
            {agent && <span className="text-sm text-gray-400">— {agent.name}</span>}
            {sessionId && (
              <Link
                href={`/canvas/live?session_id=${sessionId}`}
                className="ml-auto flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900"
                title="在实时画布查看本对话的执行轨迹(tick / 工具调用)"
              >
                <Activity className="h-3.5 w-3.5" />
                实时画布
              </Link>
            )}
          </header>

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

          <div className="border-t bg-white px-6 py-4">
            <div className="flex items-end gap-3">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={handleInput}
                onKeyDown={handleKeyDown}
                placeholder={
                  agent
                    ? "输入消息... (Enter 发送, Shift+Enter 换行)"
                    : "请先选择 Agent"
                }
                rows={1}
                aria-label="消息输入"
                className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-2.5 text-sm outline-none transition-colors focus:border-blue-400"
              />
              <button
                onClick={send}
                disabled={loading || !input.trim() || !agent}
                aria-label="发送消息"
                className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
