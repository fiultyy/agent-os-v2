import Link from "next/link";

export default function Home() {
  return (
    <main className="flex h-screen flex-col items-center justify-center gap-6">
      <h1 className="text-4xl font-bold">Agent OS</h1>
      <p className="text-gray-500">
        面向 Agent 工作流的操作系统级平台
      </p>
      <Link
        href="/canvas"
        className="rounded-lg bg-blue-600 px-6 py-3 text-white hover:bg-blue-700 transition-colors"
      >
        打开画布 →
      </Link>
    </main>
  );
}
