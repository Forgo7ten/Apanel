"use client";

import { useState } from "react";

export type ColumnManagerItem = {
  id: string;
  label: string;
  visible: boolean;
  movable?: boolean;
  toggleable?: boolean;
  width?: number;
  viewMode?: "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";
  deletable?: boolean;
  pending?: boolean;
};

export function ColumnManager({
  items,
  onToggle,
  onMove,
  onWidthChange,
  onViewModeChange,
  onDelete,
}: {
  items: ColumnManagerItem[];
  onToggle: (id: string, visible: boolean) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onWidthChange?: (id: string, width: number) => void;
  onViewModeChange?: (id: string, viewMode: ColumnManagerItem["viewMode"]) => void;
  onDelete?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [widthDrafts, setWidthDrafts] = useState<Record<string, string>>({});
  const movableItems = items.filter((item) => item.movable !== false);

  function commitWidth(item: ColumnManagerItem) {
    if (!onWidthChange) return;
    const draft = widthDrafts[item.id] ?? (item.width === undefined ? "" : String(item.width));
    if (draft.trim() === "") return;
    const width = Number(draft);
    if (Number.isFinite(width) && width >= 80 && width <= 1200) {
      onWidthChange(item.id, Math.round(width));
      setWidthDrafts((current) => ({ ...current, [item.id]: String(Math.round(width)) }));
    }
  }

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
        <div className="absolute right-0 top-10 z-20 w-[min(22rem,calc(100vw-2rem))] rounded-panel border border-line bg-panel p-3 shadow-panel" role="dialog" aria-label="列管理">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <div>
              <p className="text-xs font-semibold text-primary">显示与顺序</p>
              <p className="mt-1 text-[11px] text-muted">改动会保存到当前监控表</p>
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
                <div key={item.id} className="rounded px-1.5 py-2 hover:bg-card/70">
                  <div className="flex items-center gap-2">
                    <label className="flex min-w-0 flex-1 items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={item.visible}
                      disabled={item.pending || item.toggleable === false}
                      onChange={(event) => onToggle(item.id, event.target.checked)}
                      className="size-3.5 rounded border-line bg-card text-brand accent-brand focus:ring-brand/40"
                    />
                    <span className="truncate">{item.label}</span>
                    </label>
                    {item.pending ? <span className="text-[10px] text-brand" aria-live="polite">保存中…</span> : null}
                  </div>
                  <div className="mt-1.5 flex items-center justify-end gap-1.5">
                  {isMovable ? (
                    <div className="flex items-center gap-0.5">
                      <button
                        type="button"
                        disabled={index <= 0 || item.pending}
                        onClick={() => onMove(item.id, "up")}
                        className="rounded px-1 text-muted hover:bg-line/50 hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                        aria-label={`上移${item.label}`}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        disabled={index < 0 || index >= movableItems.length - 1 || item.pending}
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
                  {item.viewMode && onViewModeChange ? (
                    <select
                      value={item.viewMode}
                      disabled={item.pending}
                      onChange={(event) => onViewModeChange(item.id, event.target.value as ColumnManagerItem["viewMode"])}
                      aria-label={`${item.label}展示模式`}
                      className="h-6 rounded border border-line bg-card px-1 text-[10px] text-secondary outline-none focus:border-brand"
                    >
                      <option value="COMPOSITE">组合</option>
                      <option value="NUMBER">数值</option>
                      <option value="DELTA">变化</option>
                      <option value="STATUS">状态</option>
                    </select>
                  ) : null}
                  {isMovable && onWidthChange ? (
                    <input
                      type="number"
                      min="80"
                      max="1200"
                      step="1"
                      value={widthDrafts[item.id] ?? (item.width === undefined ? "" : String(item.width))}
                      disabled={item.pending}
                      onChange={(event) => setWidthDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
                      onBlur={() => commitWidth(item)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          commitWidth(item);
                        }
                      }}
                      placeholder="宽度"
                      aria-label={`${item.label}列宽`}
                      className="h-6 w-14 rounded border border-line bg-card px-1 text-center text-[10px] tabular-nums text-secondary outline-none placeholder:text-muted focus:border-brand"
                    />
                  ) : null}
                  {item.deletable && onDelete ? (
                    <button
                      type="button"
                      disabled={item.pending}
                      onClick={() => onDelete(item.id)}
                      className="rounded px-1.5 text-muted hover:bg-negative/10 hover:text-negative disabled:cursor-not-allowed disabled:opacity-30"
                      aria-label={`删除${item.label}`}
                    >
                      ×
                    </button>
                  ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
