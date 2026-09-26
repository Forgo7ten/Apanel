"use client";

import { useEffect, useRef, type RefObject } from "react";

import type { IndicatorState } from "@/api/types";
import { getColumnFields } from "@/lib/indicator-contract.mjs";
import {
  focusLoopIndex,
  isDetailCloseKey,
  isValidDetailSelection,
  orderHistoryFields,
  selectRelatedStates,
} from "@/lib/detail-contract.mjs";

import { MiniChart } from "./MiniChart";
import type { DetailNotificationHandler, DetailSelection } from "./detail-types";
import { formatMetricValue, getDirection, getFieldValue } from "./indicator-utils";
import { useIndicatorDetailsData } from "./useIndicatorDetailsData";
import { stateToneFromLevel, StateTag } from "../ui/StateTag";

export type { DetailNotificationHandler, DetailSelection } from "./detail-types";

function directionLabel(direction: unknown): string {
  if (direction === "UP") return "上升";
  if (direction === "DOWN") return "下降";
  if (direction === "FLAT") return "持平";
  return "—";
}

function ScalarSummary({ value }: { value: unknown }) {
  const current = getFieldValue(value, "value") ?? value;
  const previous = getFieldValue(value, "previous_value");
  const delta = getFieldValue(value, "delta");
  const direction = getDirection(value);
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs sm:grid-cols-4">
      <div><dt className="text-muted">当前</dt><dd className="mt-1 tabular-nums text-primary">{formatMetricValue(current)}</dd></div>
      <div><dt className="text-muted">前值</dt><dd className="mt-1 tabular-nums text-secondary">{formatMetricValue(previous)}</dd></div>
      <div><dt className="text-muted">变化</dt><dd className="mt-1 tabular-nums text-secondary">{formatMetricValue(delta)}</dd></div>
      <div><dt className="text-muted">方向</dt><dd className="mt-1 text-secondary">{directionLabel(direction)}</dd></div>
    </dl>
  );
}

function StateCurrentSummary({ state }: { state: IndicatorState }) {
  const values = state.metadata?.values;
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs sm:grid-cols-3">
        <div><dt className="text-muted">当前</dt><dd className="mt-1 text-primary">{state.active ? "ACTIVE" : state.status ?? "INACTIVE"}</dd></div>
        <div><dt className="text-muted">状态切换</dt><dd className="mt-1 text-secondary">{state.transition ? "是" : "否"}</dd></div>
        <div><dt className="text-muted">发生日期</dt><dd className="mt-1 text-secondary">{state.trade_date ?? "—"}</dd></div>
      </dl>
      {values && typeof values === "object" && !Array.isArray(values) ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line/70 pt-3 text-xs">
          {Object.entries(values as Record<string, unknown>).map(([key, value]) => (
            <div key={key}><dt className="text-muted">{key}</dt><dd className="mt-1 tabular-nums text-secondary">{formatMetricValue(value)}</dd></div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function CurrentValue({ selection, currentValue }: { selection: DetailSelection; currentValue: unknown }) {
  if (selection.kind === "state") return <StateCurrentSummary state={selection.state} />;

  const fields = getColumnFields(currentValue);
  if (selection.column.view_mode === "COMPOSITE" && fields.length > 0) {
    return (
      <div className="space-y-2">
        {orderHistoryFields(Object.fromEntries(fields) as Record<string, unknown>).map(([field, fieldValue]) => (
          <div key={field} className="rounded-panel border border-line/70 bg-card/30 px-3 py-2">
            <div className="text-xs font-medium text-secondary">{field}</div>
            <div className="mt-1"><ScalarSummary value={fieldValue} /></div>
          </div>
        ))}
      </div>
    );
  }
  return <ScalarSummary value={currentValue} />;
}

function HistoryStateList({ items }: { items: Array<{
  title?: string;
  trade_date?: string;
  status?: string;
  active?: boolean;
  transition?: boolean;
}> }) {
  if (items.length === 0) return <p className="text-xs text-muted">暂无状态历史。</p>;
  return (
    <div className="max-h-40 space-y-2 overflow-y-auto">
      {items.slice(-8).reverse().map((item, index) => (
        <div key={`${item.trade_date ?? "state"}-${item.title ?? item.status}-${index}`} className="flex items-center justify-between gap-3 text-xs">
          <span className="text-secondary">{item.title ?? item.status ?? "状态"}</span>
          <span className="text-right text-muted">
            <span className="block">{item.trade_date ?? "—"} · {item.active ? "ACTIVE" : item.status ?? "INACTIVE"}</span>
            {item.transition ? <span className="block text-brand">状态切换</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}

function focusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));
}

export function IndicatorDetailsDrawer({
  selection,
  onClose,
  restoreFocusRef,
  onCreateNotification,
}: {
  selection: DetailSelection | null;
  onClose: () => void;
  restoreFocusRef: RefObject<HTMLButtonElement | null>;
  onCreateNotification?: DetailNotificationHandler;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const data = useIndicatorDetailsData(selection);
  const open = selection !== null && isValidDetailSelection(selection);

  useEffect(() => {
    if (!open) return undefined;
    closeRef.current?.focus();
    const trigger = restoreFocusRef.current;
    const onKeyDown = (event: KeyboardEvent) => {
      if (isDetailCloseKey(event.key)) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const elements = focusableElements(dialogRef.current);
      if (elements.length === 0) return;
      const currentIndex = elements.indexOf(document.activeElement as HTMLElement);
      if (currentIndex < 0) {
        event.preventDefault();
        elements[event.shiftKey ? elements.length - 1 : 0].focus();
        return;
      }
      const atBoundary = event.shiftKey ? currentIndex === 0 : currentIndex === elements.length - 1;
      if (atBoundary) {
        event.preventDefault();
        elements[focusLoopIndex(currentIndex, elements.length, event.shiftKey)].focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      trigger?.focus();
    };
  }, [onClose, open, restoreFocusRef]);

  if (!selection || !open) return null;

  const title = selection.kind === "indicator"
    ? selection.column.title ?? selection.column.label ?? selection.column.indicator_type ?? selection.column.type
    : selection.state.title;
  const relatedStates = selection.kind === "indicator"
    ? selectRelatedStates<IndicatorState>(selection.stock.states, selection.column.indicator_type ?? selection.column.type)
    : [];

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        ref={dialogRef}
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-line bg-panel shadow-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="indicator-detail-title"
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <p id="indicator-detail-title" className="text-base font-semibold text-primary">{title}详情</p>
            <p className="mt-1 text-xs text-muted">{selection.stock.name ?? "未命名证券"} · {selection.stock.symbol} · {data.range.start} 至 {data.range.end}</p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} className="rounded p-1 text-lg leading-none text-muted hover:bg-card hover:text-primary focus:outline-none focus:ring-2 focus:ring-brand/40" aria-label="关闭指标详情">×</button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          <section aria-labelledby="indicator-current-title">
            <h2 id="indicator-current-title" className="mb-3 text-xs font-medium uppercase tracking-wide text-muted">当前观察</h2>
            <CurrentValue selection={selection} currentValue={data.currentValue} />
            {selection.kind === "state" ? <div className="mt-3"><StateTag tone={stateToneFromLevel(selection.state.level ?? selection.state.severity)}>{selection.state.title}</StateTag></div> : null}
            {selection.kind === "indicator" && relatedStates.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-1.5" aria-label="相关状态">
                {relatedStates.map((state) => <StateTag key={state.state_id} tone={stateToneFromLevel(state.level ?? state.severity)}>{state.title}</StateTag>)}
              </div>
            ) : null}
          </section>

          {selection.kind === "indicator" ? (
            <section aria-labelledby="indicator-history-title" className="rounded-panel border border-line bg-card/30 p-3">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 id="indicator-history-title" className="text-xs font-medium uppercase tracking-wide text-muted">90 日指标历史</h2>
                <span className="text-[11px] text-muted">{data.indicatorItems.length} 个匹配点</span>
              </div>
              {data.indicatorState === "loading" ? <p className="text-xs text-muted" aria-busy="true">正在加载历史…</p> : null}
              {data.indicatorState === "error" ? <p className="text-xs text-negative" role="alert">历史加载失败，请稍后重试。</p> : null}
              {data.indicatorState === "empty" ? <p className="text-xs text-muted">暂无该参数的历史数据。</p> : null}
              {data.indicatorState === "ready" ? <MiniChart points={data.chartPoints} /> : null}
              {data.indicatorState === "ready" && data.chartPoints.length === 0 ? <p className="text-xs text-muted">历史中没有可绘制的数值。</p> : null}
              {data.indicatorState === "ready" ? <p className="mt-2 text-[11px] text-muted">最近值 {formatMetricValue(data.latestValue)}</p> : null}
            </section>
          ) : null}

          <section aria-labelledby="state-history-title" className="rounded-panel border border-line bg-card/30 p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 id="state-history-title" className="text-xs font-medium uppercase tracking-wide text-muted">90 日状态历史</h2>
              <span className="text-[11px] text-muted">{data.stateItems.length} 条记录</span>
            </div>
            {data.stateQuery.isPending ? <p className="text-xs text-muted" aria-busy="true">正在加载状态历史…</p> : null}
            {data.stateQuery.isError ? <p className="text-xs text-negative" role="alert">状态历史加载失败，请稍后重试。</p> : null}
            {!data.stateQuery.isPending && !data.stateQuery.isError ? <HistoryStateList items={data.stateItems} /> : null}
          </section>
          {onCreateNotification ? (
            <button type="button" onClick={() => onCreateNotification(selection)} className="w-full rounded-panel border border-brand/40 bg-brand/10 px-3 py-2 text-xs font-medium text-brand hover:bg-brand/20 focus:outline-none focus:ring-2 focus:ring-brand/40">
              创建通知
            </button>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
