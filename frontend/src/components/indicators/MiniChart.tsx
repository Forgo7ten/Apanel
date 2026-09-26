"use client";

import { miniChartGeometry, miniChartPointLabel } from "@/lib/detail-contract.mjs";

export type MiniChartPoint = { date: string; value: number };

export function MiniChart({ points }: { points: MiniChartPoint[] }) {
  const geometry = miniChartGeometry(points) as Array<MiniChartPoint & { x: number; y: number }>;
  if (geometry.length === 0) {
    return <p className="rounded-panel border border-line/70 bg-card/40 px-3 py-5 text-center text-xs text-muted">暂无可绘制的历史数值。</p>;
  }

  const polyline = geometry.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <svg
      viewBox="0 0 280 72"
      className="h-20 w-full overflow-visible"
      role="img"
      aria-label={`历史数据 ${points.length} 个点`}
    >
      <line x1="0" y1="71" x2="280" y2="71" stroke="currentColor" className="text-line" strokeWidth="1" />
      {geometry.length > 1 ? <polyline points={polyline} fill="none" stroke="currentColor" className="text-brand" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /> : null}
      {geometry.map((point) => (
        <circle
          key={`${point.date}-${point.x}`}
          cx={point.x}
          cy={point.y}
          r="2.5"
          fill="currentColor"
          className="text-brand outline-none focus:outline-none focus:ring-2 focus:ring-brand"
          tabIndex={0}
          role="img"
          aria-label={miniChartPointLabel(point)}
        >
          <title>{miniChartPointLabel(point)}</title>
        </circle>
      ))}
    </svg>
  );
}
