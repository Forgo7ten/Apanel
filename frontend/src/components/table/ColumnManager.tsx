"use client";

import { useState } from "react";

export type ColumnManagerItem = {
  id: string;
  label: string;
  visible: boolean;
  movable?: boolean;
};

export function ColumnManager({
  items,
  onToggle,
  onMove,
}: {
  items: ColumnManagerItem[];
  onToggle: (id: string, visible: boolean) => void;
  onMove: (id: string, direction: "up" | "down") => void;
}) {
  const [open, setOpen] = useState(false);
  const movableItems = items.filter((item) => item.movable !== false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="inline-flex h-8 items-center gap-2 rounded-panel border border-line bg-card px-2.5 text-xs font-medium text-secondary transition hover:border-brand/60 hover:text-primary focus:outline-none focus:ring-2 focus:ring-brand/40"
      >
        <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />
          <path strokeLinecap="round" d="M8 4v4M16 10v4M10 16v4" />
        </svg>
        列管理
      </button>

      {open ? (
        <div className="absolute right-0 top-10 z-20 w-72 rounded-panel border border-line bg-panel p-3 shadow-panel" role="dialog" aria-label="列管理">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <div>
              <p className="text-xs font-semibold text-primary">显示与顺序</p>
              <p className="mt-1 text-[11px] text-muted">只调整当前浏览器中的表格视图</p>
            </div>
            <button type="button" onClick={() => setOpen(false)} className="rounded p-1 text-muted hover:bg-card hover:text-primary" aria-label="关闭列管理">
              ×
            </button>
          </div>
          <div className="mt-2 space-y-1">
            {items.map((item) => {
              const index = movableItems.findIndex((candidate) => candidate.id === item.id);
              const isMovable = item.movable !== false;
              return (
                <div key={item.id} className="flex items-center gap-2 rounded px-1.5 py-1.5 hover:bg-card/70">
                  <label className="flex min-w-0 flex-1 items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={item.visible}
                      onChange={(event) => onToggle(item.id, event.target.checked)}
                      className="size-3.5 rounded border-line bg-card text-brand accent-brand focus:ring-brand/40"
                    />
                    <span className="truncate">{item.label}</span>
                  </label>
                  {isMovable ? (
                    <div className="flex items-center gap-0.5">
                      <button
                        type="button"
                        disabled={index <= 0}
                        onClick={() => onMove(item.id, "up")}
                        className="rounded px-1 text-muted hover:bg-line/50 hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                        aria-label={`上移${item.label}`}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        disabled={index < 0 || index >= movableItems.length - 1}
                        onClick={() => onMove(item.id, "down")}
                        className="rounded px-1 text-muted hover:bg-line/50 hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                        aria-label={`下移${item.label}`}
                      >
                        ↓
                      </button>
                    </div>
                  ) : (
                    <span className="text-[10px] text-muted">固定</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
