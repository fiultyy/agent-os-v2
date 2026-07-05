"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// ── 扁平化导航:全部页面一行铺开,未完工的挂"演示"badge ──────────────
// 之前 Header 只露 Agents / Flows 两个入口,/canvas/live、/memory、/login
// 全藏在角落(无导航)。现在统一露出,让用户一眼看到系统能干什么。
// /canvas 与 /flows 是同一个 FlowCanvas 设计画布(冗余路由),故导航只放
// /flows 一个入口,/canvas 仍可由 /agents 卡片点击进入。
interface NavItem {
  href: string;
  label: string;
  demo?: boolean; // 演示级功能:可视化原型,执行链路未通
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "对话" },
  { href: "/agents", label: "Agents" },
  { href: "/flows", label: "设计画布", demo: true },
  { href: "/canvas/live", label: "实时画布" },
  { href: "/memory", label: "记忆调试" },
  { href: "/login", label: "登录" },
];

export function Header() {
  const pathname = usePathname();
  return (
    <header className="flex h-14 items-center justify-between border-b px-4">
      <div className="flex items-center gap-6">
        <Link href="/" className="font-semibold text-gray-900">
          Agent OS
        </Link>
        <nav className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-1.5 transition-colors hover:text-gray-900 ${
                  active ? "font-medium text-gray-900" : "text-gray-500"
                }`}
              >
                <span
                  className={
                    active
                      ? "border-b-2 border-blue-500 pb-0.5"
                      : item.demo
                      ? "border-b-2 border-transparent pb-0.5"
                      : ""
                  }
                >
                  {item.label}
                </span>
                {item.demo && (
                  <span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] font-medium text-amber-700">
                    演示
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="text-sm text-gray-400">Agent OS v0.1</div>
    </header>
  );
}
