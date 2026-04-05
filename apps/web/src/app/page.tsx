import Link from "next/link";
import { Bot, Layout, Brain } from "lucide-react";

export default function Home() {
  const cards = [
    { href: "/agents", label: "Agent 管理", desc: "创建和管理智能体", icon: <Bot className="h-6 w-6" /> },
    { href: "/canvas", label: "流程画布", desc: "可视化编排 Agent 工作流", icon: <Layout className="h-6 w-6" /> },
    { href: "/memory", label: "记忆 & 调试", desc: "查看记忆、通信和执行历史", icon: <Brain className="h-6 w-6" /> },
  ];

  return (
    <main className="flex h-screen flex-col items-center justify-center gap-8">
      <div className="text-center">
        <h1 className="text-4xl font-bold">Agent OS</h1>
        <p className="mt-2 text-gray-500">面向 Agent 工作流的操作系统级平台</p>
      </div>
      <div className="flex gap-4">
        {cards.map((card) => (
          <Link
            key={card.href}
            href={card.href}
            className="flex w-52 flex-col items-center gap-3 rounded-lg border bg-white p-6 shadow-sm transition-shadow hover:shadow-md"
          >
            <div className="text-blue-600">{card.icon}</div>
            <div className="text-center">
              <div className="font-semibold">{card.label}</div>
              <div className="mt-1 text-xs text-gray-400">{card.desc}</div>
            </div>
          </Link>
        ))}
      </div>
    </main>
  );
}
