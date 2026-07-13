"use client";

/**
 * /observe 路由级错误边界(App Router 约定)。
 * 任一子组件(observe-api / SessionList / EventTimeline 等)抛异常时,
 * 显示局部错误提示而非 Next.js 全屏 "Application error",便于 qa-farm 重试。
 */
import { useEffect } from "react";

export default function ObserveError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[observe] route error boundary:", error);
  }, [error]);

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-md rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <h2 className="text-base font-semibold text-red-700">观测操作台出错</h2>
        <p className="mt-2 text-xs text-red-600">
          {error?.message || "加载 observe 数据时发生异常"}
        </p>
        <button
          onClick={reset}
          className="mt-4 rounded bg-red-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
        >
          重试
        </button>
      </div>
    </div>
  );
}
