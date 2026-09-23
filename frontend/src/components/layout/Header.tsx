"use client";

import { usePathname } from "next/navigation";

import { getPageMeta } from "@/lib/navigation";

import { NavIcon } from "./NavIcon";

export function Header() {
  const pathname = usePathname() ?? "/watch";
  const page = getPageMeta(pathname);

  return (
    <header className="border-b border-line bg-app/80 px-page py-4 backdrop-blur-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted">
          <NavIcon name={page.icon} className="size-4" />
          <span>工作台</span>
          <span className="text-line">/</span>
          <span className="truncate text-secondary">{page.label}</span>
        </div>
        <div role="status" className="flex items-center gap-2 rounded-full border border-line bg-panel px-2.5 py-1.5 text-[11px] text-muted">
          <span aria-hidden="true" className="size-1.5 rounded-full bg-positive" />
          <span>界面就绪 · 后端状态待接入</span>
        </div>
      </div>
    </header>
  );
}
