"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname } from "next/navigation";

import { getBackendHealth } from "@/api/health";
import type { BackendHealth } from "@/api/types";
import { getPageMeta } from "@/lib/navigation";

import { NavIcon } from "./NavIcon";

const HEALTH_REFRESH_INTERVAL_MS = 30_000;

function dependencyLabel(name: string): string {
  if (name === "postgres") return "PostgreSQL";
  if (name === "redis") return "Redis";
  return name;
}

function healthDetail(health: BackendHealth | undefined): string | undefined {
  if (!health) return undefined;
  return Object.entries(health.dependencies)
    .map(([name, dependency]) => `${dependencyLabel(name)} ${dependency.status === "healthy" ? "正常" : "异常"}`)
    .join(" · ");
}

export function Header() {
  const pathname = usePathname() ?? "/watch";
  const page = getPageMeta(pathname);
  const healthQuery = useQuery({
    queryKey: ["backend-health"],
    queryFn: getBackendHealth,
    refetchInterval: HEALTH_REFRESH_INTERVAL_MS,
    refetchIntervalInBackground: false,
    retry: 1,
    staleTime: 15_000,
  });
  const status = healthQuery.isPending
    ? "checking"
    : healthQuery.isError
      ? "offline"
      : healthQuery.data?.status === "degraded"
        ? "degraded"
        : "healthy";
  const statusLabel = status === "checking"
    ? "正在检查后端…"
    : status === "offline"
      ? "后端不可达"
      : status === "degraded"
        ? "后端服务降级"
        : "后端服务正常";
  const dotClass = status === "healthy"
    ? "bg-positive"
    : status === "degraded"
      ? "bg-warning"
      : status === "offline"
        ? "bg-negative"
        : "bg-muted";
  const detail = healthDetail(healthQuery.data);

  return (
    <header className="border-b border-line bg-app/80 px-page py-4 backdrop-blur-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted">
          <NavIcon name={page.icon} className="size-4" />
          <span>工作台</span>
          <span className="text-line">/</span>
          <span className="truncate text-secondary">{page.label}</span>
        </div>
        <div
          role="status"
          title={detail}
          aria-label={detail ? `${statusLabel}；${detail}` : statusLabel}
          className="flex items-center gap-2 rounded-full border border-line bg-panel px-2.5 py-1.5 text-[11px] text-muted"
        >
          <span aria-hidden="true" className={`size-1.5 rounded-full ${dotClass}`} />
          <span>{statusLabel}</span>
        </div>
      </div>
    </header>
  );
}
