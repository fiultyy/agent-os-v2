"use client";

import { AlertTriangle } from "lucide-react";

/**
 * 演示功能提示条 —— 标记未完工的原型页面。
 *
 * 用于 FlowCanvas 设计画布等"可视化已具备但执行链路未通"的功能,
 * 让用户一眼知道这是演示原型、边界在哪,避免误以为能正常编排。
 */
export function DemoBanner({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
      <div className="flex flex-wrap items-center gap-x-2">
        <span className="rounded bg-amber-200 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-900">
          演示
        </span>
        <span className="font-semibold text-amber-900">{title}</span>
        {children && <span className="text-amber-700">{children}</span>}
      </div>
    </div>
  );
}
