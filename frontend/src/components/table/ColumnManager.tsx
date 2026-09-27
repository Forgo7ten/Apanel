"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Identifier } from "@/api/types";

import { trapDialogTab } from "../ui/dialog-focus";
import { ColumnAddForm } from "./ColumnAddForm";

export type ColumnManagerItem = {
  id: string;
  label: string;
  visible: boolean;
  movable?: boolean;
  toggleable?: boolean;
  width?: number;
  viewMode?: "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";
  viewModeLocked?: boolean;
  deletable?: boolean;
  pending?: boolean;
};

export function ColumnManager({
  tableId,
  items,
  onToggle,
  onMove,
  onReorder,
  onWidthChange,
  onViewModeChange,
  onDelete,
}: {
  tableId: Identifier;
  items: ColumnManagerItem[];
  onToggle: (id: string, visible: boolean) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onReorder: (sourceId: string, targetId: string, position: "before" | "after") => void;
  onWidthChange?: (id: string, width: number) => void;
  onViewModeChange?: (id: string, viewMode: ColumnManagerItem["viewMode"]) => void;
  onDelete?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<"columns" | "add">("columns");
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [dragOverPosition, setDragOverPosition] = useState<"before" | "after">("before");
  const [widthDrafts, setWidthDrafts] = useState<Record<string, string>>({});
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  const closeManager = useCallback(() => {
    setOpen(false);
    setPanel("columns");
    setDraggedId(null);
    setDragOverId(null);
    setDragOverPosition("before");
  }, []);

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const dialog = dialogRef.current;
    const firstToggle = dialog?.querySelector<HTMLInputElement>('input[type="checkbox"]:not([disabled])');
    const fallbackFocus = dialog?.querySelector<HTMLButtonElement>("button:not([disabled])");
    (firstToggle ?? fallbackFocus)?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeManager();
        return;
      }
      if (event.key === "Tab" && dialogRef.current && trapDialogTab(dialogRef.current, event.shiftKey)) {
        event.preventDefault();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      trigger?.focus();
    };
  }, [closeManager, open]);

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
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => open ? closeManager() : setOpen(true)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="inline-flex h-8 items-center gap-2 rounded-panel border border-line bg-card px-2.5 text-xs font-medium text-secondary transition hover:border-brand/60 hover:text-primary focus:outline-none focus:ring-2 focus:ring-brand/40"
      >
        <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />
          <path strokeLinecap="round" d="M8 4v4M16 10v4M10 16v4" />
        </svg>
        列管理
      </button>

      {open ? createPortal(
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 px-4 py-6"
          role="presentation"
          onMouseDown={(event) => event.target === event.currentTarget && closeManager()}
        >
          <section
            ref={dialogRef}
            className="flex max-h-[min(82vh,48rem)] w-full max-w-2xl flex-col overflow-hidden rounded-panel border border-line bg-panel shadow-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="column-manager-title"
          >
            <div className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-4">
              <div>
                <p id="column-manager-title" className="text-base font-semibold text-primary">列管理</p>
                <p className="mt-1 text-xs leading-5 text-muted">统一添加、显示、排序和调整当前监控表的指标列。</p>
              </div>
              <button type="button" onClick={closeManager} className="rounded p-1 text-muted hover:bg-card hover:text-primary" aria-label="关闭列管理">
                ×
              </button>
            </div>

            <div className="flex shrink-0 gap-1 border-b border-line px-4 pt-3">
              <button
                type="button"
                onClick={() => setPanel("columns")}
                className={`rounded-t-panel px-3 py-2 text-xs font-medium transition ${panel === "columns" ? "bg-card text-primary" : "text-muted hover:text-secondary"}`}
                aria-current={panel === "columns" ? "page" : undefined}
              >
                现有列
              </button>
              <button
                type="button"
                onClick={() => setPanel("add")}
                className={`rounded-t-panel px-3 py-2 text-xs font-medium transition ${panel === "add" ? "bg-card text-primary" : "text-muted hover:text-secondary"}`}
                aria-current={panel === "add" ? "page" : undefined}
              >
                + 添加指标列
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {panel === "add" ? (
                <ColumnAddForm tableId={tableId} onCreated={() => setPanel("columns")} />
              ) : (
                <div className="space-y-1">
                  <p className="px-1 pb-2 text-[11px] leading-5 text-muted">拖动动态指标列左侧手柄可调整顺序；固定列不可移动或隐藏。</p>
                  {items.map((item) => {
                    const isMovable = item.movable !== false;
                    const isDragging = draggedId === item.id;
                    const isDragTarget = dragOverId === item.id && draggedId !== item.id;
                    return (
                    <div
                      key={item.id}
                      onDragOver={(event) => {
                        if (!isMovable || !draggedId || draggedId === item.id) return;
                        event.preventDefault();
                        setDragOverId(item.id);
                        const bounds = event.currentTarget.getBoundingClientRect();
                        setDragOverPosition(event.clientY >= bounds.top + bounds.height / 2 ? "after" : "before");
                      }}
                      onDrop={(event) => {
                        if (!isMovable || !draggedId || draggedId === item.id) return;
                        event.preventDefault();
                        onReorder(draggedId, item.id, dragOverPosition);
                        setDraggedId(null);
                        setDragOverId(null);
                        setDragOverPosition("before");
                      }}
                      className={`rounded-panel border px-3 py-3 transition ${isDragTarget ? "border-brand/60 bg-brand/5" : "border-transparent hover:border-line hover:bg-card/70"} ${isDragging ? "opacity-50" : ""} ${isDragTarget && dragOverPosition === "before" ? "border-t-brand" : ""} ${isDragTarget && dragOverPosition === "after" ? "border-b-brand" : ""}`}
                    >
                      <div className="flex items-center gap-3">
                        {isMovable ? (
                          <button
                            type="button"
                            draggable={!item.pending}
                            disabled={item.pending}
                            onDragStart={(event) => {
                              setDraggedId(item.id);
                              setDragOverId(null);
                              event.dataTransfer.effectAllowed = "move";
                              event.dataTransfer.setData("text/plain", item.id);
                            }}
                            onDragEnd={() => {
                              setDraggedId(null);
                              setDragOverId(null);
                              setDragOverPosition("before");
                            }}
                            onKeyDown={(event) => {
                              if (event.key === "ArrowUp") {
                                event.preventDefault();
                                onMove(item.id, "up");
                              } else if (event.key === "ArrowDown") {
                                event.preventDefault();
                                onMove(item.id, "down");
                              }
                            }}
                            className="inline-flex h-8 shrink-0 cursor-grab items-center gap-1 rounded border border-line bg-card px-2 text-[11px] text-muted hover:border-brand/50 hover:text-primary active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-40"
                            aria-label={`拖拽排序${item.label}，键盘可用上下方向键移动`}
                            title="拖拽排序"
                          >
                            <span aria-hidden="true">⋮⋮</span>
                          </button>
                        ) : null}
                        <label className="flex min-w-0 flex-1 items-center gap-3 text-sm text-secondary">
                          <input
                            type="checkbox"
                            checked={item.visible}
                            disabled={item.pending || item.toggleable === false}
                            onChange={(event) => onToggle(item.id, event.target.checked)}
                            className="size-4 shrink-0 rounded border-line bg-card text-brand accent-brand focus:ring-brand/40"
                          />
                          <span className="min-w-0 flex-1 truncate" title={item.label}>{item.label}</span>
                        </label>
                        {item.pending ? <span className="shrink-0 text-[11px] text-brand" aria-live="polite">保存中…</span> : null}
                        {!isMovable ? <span className="shrink-0 rounded bg-card px-2 py-1 text-[10px] text-muted">固定列</span> : null}
                      </div>

                      {isMovable ? (
                        <div className="mt-2.5 flex flex-wrap items-center justify-end gap-2 pl-12">
                          {item.viewMode && onViewModeChange ? (
                            <select
                              value={item.viewMode}
                              disabled={item.pending || item.viewModeLocked}
                              onChange={(event) => onViewModeChange(item.id, event.target.value as ColumnManagerItem["viewMode"])}
                              aria-label={`${item.label}展示模式`}
                              className="h-7 rounded border border-line bg-card px-2 text-[11px] text-secondary outline-none focus:border-brand disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {item.viewModeLocked ? (
                                <option value="NUMBER">数值（固定）</option>
                              ) : (
                                <>
                                  <option value="COMPOSITE">组合</option>
                                  <option value="NUMBER">数值</option>
                                  <option value="DELTA">变化</option>
                                  <option value="STATUS">状态</option>
                                </>
                              )}
                            </select>
                          ) : null}
                          {onWidthChange ? (
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
                              className="h-7 w-16 rounded border border-line bg-card px-2 text-center text-[11px] tabular-nums text-secondary outline-none placeholder:text-muted focus:border-brand"
                            />
                          ) : null}
                          {item.deletable && onDelete ? (
                            <button
                              type="button"
                              disabled={item.pending}
                              onClick={() => onDelete(item.id)}
                              className="h-7 rounded border border-negative/20 px-2 text-[11px] text-muted hover:bg-negative/10 hover:text-negative disabled:cursor-not-allowed disabled:opacity-30"
                              aria-label={`删除${item.label}`}
                            >
                              删除
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                  })}
                </div>
              )}
            </div>

            <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-5 py-3">
              <p className="text-[11px] text-muted">{panel === "add" ? "新增列后会自动返回现有列列表。" : "修改会立即保存到当前监控表。"}</p>
              <button type="button" onClick={closeManager} className="rounded-panel border border-line bg-card px-3 py-2 text-xs font-medium text-secondary hover:text-primary">
                完成
              </button>
            </div>
          </section>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
