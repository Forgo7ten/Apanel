"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { navItems } from "@/lib/navigation";

import { NavIcon } from "./NavIcon";

function isActivePath(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
  const pathname = usePathname() ?? "/watch";

  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-line bg-panel lg:sticky lg:top-0 lg:h-screen lg:w-60 lg:border-b-0 lg:border-r">
      <div className="flex items-center gap-3 px-5 py-5 lg:px-6 lg:py-6">
        <div className="grid size-9 place-items-center rounded-panel bg-brand text-sm font-bold text-white shadow-panel">
          A
        </div>
        <div>
          <p className="text-sm font-semibold tracking-wide text-primary">Apanel</p>
          <p className="mt-0.5 text-[11px] uppercase tracking-[0.18em] text-muted">Signal workspace</p>
        </div>
      </div>

      <nav aria-label="主导航" className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:gap-2 lg:px-3 lg:py-4">
        <p className="hidden px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted lg:block">Workspace</p>
        {navItems.map((item) => {
          const active = isActivePath(pathname, item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`group flex min-w-max items-center gap-3 rounded-panel px-3 py-2.5 transition-colors lg:min-w-0 ${
                active
                  ? "bg-card text-primary shadow-[inset_2px_0_0_rgb(var(--color-brand)/1)]"
                  : "text-secondary hover:bg-card/70 hover:text-primary"
              }`}
            >
              <NavIcon name={item.icon} className={`size-[18px] shrink-0 ${active ? "text-brand" : "text-muted group-hover:text-secondary"}`} />
              <span className="flex min-w-0 flex-col">
                <span className="text-sm font-medium">{item.label}</span>
                <span className="mt-0.5 hidden text-[11px] text-muted lg:block">{item.description}</span>
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="hidden border-t border-line px-5 py-5 lg:block">
        <div className="flex items-center gap-3">
          <div className="grid size-8 place-items-center rounded-full border border-line bg-card text-xs font-semibold text-secondary">访</div>
          <div className="min-w-0">
            <p className="truncate text-xs font-medium text-primary">访客工作区</p>
            <p className="mt-0.5 truncate text-[11px] text-muted">Sprint 0 · 本地环境</p>
          </div>
          <span role="status" className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-muted">
            <span aria-hidden="true" className="size-2 rounded-full bg-positive" />
            <span>界面就绪</span>
          </span>
        </div>
      </div>
    </aside>
  );
}
