export default function Home() {
  return (
    <main className="flex h-screen flex-col">
      <h1 className="p-4 text-2xl font-bold">Agent OS</h1>
      <p className="px-4 text-gray-500">
        面向 Agent 工作流的操作系统级平台
      </p>
      <div className="flex-1 p-4">
        <a
          href="/canvas"
          className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
        >
          打开画布 →
        </a>
      </div>
    </main>
  );
}
