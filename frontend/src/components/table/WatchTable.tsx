"use client";

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type ColumnDef,
  type Header,
  type SortingState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  Identifier,
  IndicatorState,
  IndicatorViewMode,
  UpdateColumnInput,
  WatchTableColumn,
  WatchTableDetails,
  WatchTableStock,
  WatchTableSummary,
} from "@/api/types";
import {
  deleteWatchTableColumn,
  removeStockFromWatchTable,
  reorderWatchTableColumns,
  updateWatchTableColumn,
} from "@/api/watch";
import { getColumnTitle, getIndicatorValue } from "@/components/indicators/indicator-utils";
import { IndicatorCell } from "@/components/indicators/IndicatorCell";
import { isApiError } from "@/lib/api-errors";
import { isDetailActivationKey } from "@/lib/detail-contract.mjs";
import { getColumnFields } from "@/lib/indicator-contract.mjs";
import {
  calculateVirtualRange,
  toColumnOrderPayload,
  virtualScrollTopForKey,
  WATCH_COMPOSITE_LAYOUT,
  watchRowLayoutContract,
} from "@/lib/watch-contract.mjs";
import { useWatchStore } from "@/stores/watch-store";
import { stateToneFromLevel, StateTag } from "@/components/ui/StateTag";
import {
  IndicatorDetailsDrawer,
  type DetailNotificationHandler,
  type DetailSelection,
} from "@/components/indicators/IndicatorDetailsDrawer";

import { AddColumnDialog } from "./AddColumnDialog";
import { ColumnManager, type ColumnManagerItem } from "./ColumnManager";

const WATCH_OVERSCAN = 4;
const DEFAULT_VIEWPORT_HEIGHT = 560;

function columnId(column: WatchTableColumn, index: number): string {
  return String(column.id ?? column.key ?? column.indicator_type ?? `${column.type}-${index}`);
}

function sortValue(value: unknown): string | number {
  if (typeof value === "number") return value;
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const candidate = record.value ?? record.price ?? record.current ?? record.status ?? record.title;
    if (typeof candidate === "number" || typeof candidate === "string") return candidate;
  }
  return "";
}

function compareRows(rowA: WatchTableStock, rowB: WatchTableStock, columnIdValue: string): number {
  const valueA = columnIdValue === "security" ? rowA.name ?? rowA.security?.name ?? rowA.symbol : columnIdValue === "price" ? rowA.price : undefined;
  const valueB = columnIdValue === "security" ? rowB.name ?? rowB.security?.name ?? rowB.symbol : columnIdValue === "price" ? rowB.price : undefined;
  const a = sortValue(valueA ?? "");
  const b = sortValue(valueB ?? "");

  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "zh-CN", { numeric: true });
}

function renderSortState(header: Header<WatchTableStock, unknown>): string {
  const sorted = header.column.getIsSorted();
  return sorted === "asc" ? " ↑" : sorted === "desc" ? " ↓" : "";
}

function SortableHeader({ header }: { header: Header<WatchTableStock, unknown> }) {
  const canSort = header.column.getCanSort();
  const label = flexRender(header.column.columnDef.header, header.getContext());

  if (!canSort) return <>{label}</>;

  return (
    <button
      type="button"
      onClick={header.column.getToggleSortingHandler()}
      className="inline-flex items-center gap-1 text-left transition-colors hover:text-primary focus:outline-none focus:ring-2 focus:ring-brand/40"
      aria-label={`按${String(label)}排序`}
    >
      <span>{label}</span>
      <span className="text-brand">{renderSortState(header)}</span>
    </button>
  );
}

function StateList({ states, onOpen }: { states: IndicatorState[]; onOpen?: (state: IndicatorState, trigger: HTMLButtonElement) => void }) {
  if (states.length === 0) return <span className="text-xs text-muted">—</span>;

  return (
    <div className="flex h-5 max-w-[260px] flex-nowrap items-center gap-1 overflow-x-auto">
      {states.slice(0, WATCH_COMPOSITE_LAYOUT.maxStateTags).map((state) => (
        <button
          key={state.state_id}
          type="button"
          onClick={(event) => onOpen?.(state, event.currentTarget)}
          onKeyDown={(event) => {
            if (!isDetailActivationKey(event.key)) return;
            event.preventDefault();
            onOpen?.(state, event.currentTarget);
          }}
          className="rounded-full focus:outline-none focus:ring-2 focus:ring-brand/40"
          aria-label={`查看状态${state.title}详情`}
        >
          <StateTag compact tone={stateToneFromLevel(state.level ?? state.severity)}>{state.title}</StateTag>
        </button>
      ))}
      {states.length > WATCH_COMPOSITE_LAYOUT.maxStateTags ? <span className="shrink-0 self-center text-[11px] text-muted">+{states.length - WATCH_COMPOSITE_LAYOUT.maxStateTags}</span> : null}
    </div>
  );
}

function buildColumnDefs(
  columns: WatchTableColumn[],
  onOpenIndicator: (stock: WatchTableStock, column: WatchTableColumn, trigger: HTMLButtonElement) => void,
  onOpenState: (stock: WatchTableStock, state: IndicatorState, trigger: HTMLButtonElement) => void,
): ColumnDef<WatchTableStock, unknown>[] {
  const dynamicColumns = columns.map((column, index) => {
    const id = columnId(column, index);
    return {
      id,
      accessorFn: (stock: WatchTableStock) => getIndicatorValue(stock, column),
      header: getColumnTitle(column),
      sortingFn: (rowA, rowB, sortingColumnId) => {
        const valueA = sortValue(rowA.getValue(sortingColumnId));
        const valueB = sortValue(rowB.getValue(sortingColumnId));
        if (typeof valueA === "number" && typeof valueB === "number") return valueA - valueB;
        return String(valueA).localeCompare(String(valueB), "zh-CN", { numeric: true });
      },
      cell: ({ row }) => (
        <button
          type="button"
          onClick={(event) => onOpenIndicator(row.original, column, event.currentTarget)}
          onKeyDown={(event) => {
            if (!isDetailActivationKey(event.key)) return;
            event.preventDefault();
            onOpenIndicator(row.original, column, event.currentTarget);
          }}
          className="rounded-panel text-left focus:outline-none focus:ring-2 focus:ring-brand/40"
          aria-label={`查看${getColumnTitle(column)}详情`}
        >
          <IndicatorCell mode={column.view_mode} value={getIndicatorValue(row.original, column)} states={row.original.states} />
        </button>
      ),
      meta: { label: getColumnTitle(column), movable: true, width: column.width },
    } satisfies ColumnDef<WatchTableStock, unknown>;
  });

  return [
    {
      id: "security",
      accessorFn: (stock) => stock.name ?? stock.security?.name ?? stock.symbol,
      header: "股票",
      sortingFn: (rowA, rowB) => compareRows(rowA.original, rowB.original, "security"),
      cell: ({ row }) => {
        const stock = row.original;
        return (
          <div className="min-w-[150px] whitespace-nowrap">
            <p className="font-medium text-primary">{stock.name ?? stock.security?.name ?? "未命名证券"}</p>
            <p className="mt-1 font-mono text-[11px] text-muted">{stock.symbol}</p>
          </div>
        );
      },
      meta: { label: "股票", movable: false, toggleable: false },
    },
    {
      id: "price",
      accessorFn: (stock) => stock.price,
      header: "当前价",
      sortingFn: (rowA, rowB) => compareRows(rowA.original, rowB.original, "price"),
      cell: ({ row }) => <IndicatorCell mode="DELTA" value={row.original.price} />,
      meta: { label: "当前价", movable: false, toggleable: false },
    },
    ...dynamicColumns,
    {
      id: "states",
      accessorFn: (stock) => stock.states?.map((state) => state.title).join(" ") ?? "",
      header: "状态",
      cell: ({ row }) => <StateList states={row.original.states ?? []} onOpen={(state, trigger) => onOpenState(row.original, state, trigger)} />,
      meta: { label: "状态", movable: false, toggleable: false },
    },
  ];
}

function buildActionColumn({
  onRemove,
  pendingSecurityId,
}: {
  onRemove?: (securityId: Identifier) => void;
  pendingSecurityId?: Identifier;
}): ColumnDef<WatchTableStock, unknown> {
  return {
    id: "actions",
    header: "操作",
    enableSorting: false,
    cell: ({ row }) => {
      const securityId = row.original.security_id;
      if (securityId === undefined || securityId === null || !onRemove) return <span className="text-xs text-muted">—</span>;
      const pending = pendingSecurityId !== undefined && String(pendingSecurityId) === String(securityId);
      return (
        <button
          type="button"
          disabled={pending}
          onClick={() => onRemove(securityId)}
          className="rounded px-2 py-1 text-xs text-muted transition hover:bg-negative/10 hover:text-negative disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`删除${row.original.name ?? row.original.symbol}`}
        >
          {pending ? "删除中…" : "移除"}
        </button>
      );
    },
    meta: { label: "操作", movable: false, toggleable: false },
  } satisfies ColumnDef<WatchTableStock, unknown>;
}

function toVisibility(columns: WatchTableColumn[]): VisibilityState {
  return Object.fromEntries(columns.map((column, index) => [columnId(column, index), column.hidden !== true && column.visible !== false]));
}

function columnWidthStyle(columns: WatchTableColumn[], id: string): React.CSSProperties | undefined {
  const source = columns.find((column, index) => columnId(column, index) === id);
  return source?.width ? { width: `${source.width}px`, minWidth: `${source.width}px` } : undefined;
}

function reorderDynamicColumns(columnOrder: string[], id: string, direction: "up" | "down", dynamicIds: string[]): string[] {
  const nextDynamic = [
    ...columnOrder.filter((columnIdValue) => dynamicIds.includes(columnIdValue)),
    ...dynamicIds.filter((dynamicId) => !columnOrder.includes(dynamicId)),
  ];
  const index = nextDynamic.indexOf(id);
  const nextIndex = direction === "up" ? index - 1 : index + 1;
  if (index < 0 || nextIndex < 0 || nextIndex >= nextDynamic.length) return columnOrder;
  [nextDynamic[index], nextDynamic[nextIndex]] = [nextDynamic[nextIndex], nextDynamic[index]];
  return ["security", "price", ...nextDynamic, "states", "actions"];
}

function persistentColumnId(value: string): Identifier | null {
  if (/^\d+$/.test(value)) return Number(value);
  return null;
}

function mutationErrorMessage(error: unknown): string | null {
  if (!error) return null;
  if (isApiError(error)) {
    if (error.status === 404) return "当前资源不存在或无权限，请刷新监控表后重试。";
    if (error.status === 409) return "该操作与当前监控表状态冲突，请刷新后重试。";
    return error.message;
  }
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function WatchTable({
  table,
  onAddStock,
  onCreateNotification,
}: {
  table: WatchTableDetails;
  onAddStock?: (trigger?: HTMLButtonElement) => void;
  onCreateNotification?: DetailNotificationHandler;
}) {
  const queryClient = useQueryClient();
  const [addColumnOpen, setAddColumnOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [detailSelection, setDetailSelection] = useState<DetailSelection | null>(null);
  const addColumnTriggerRef = useRef<HTMLButtonElement | null>(null);
  const closeAddColumnDialog = useCallback(() => setAddColumnOpen(false), []);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(DEFAULT_VIEWPORT_HEIGHT);
  const tableId = table.id;

  const openIndicatorDetail = useCallback((stock: WatchTableStock, column: WatchTableColumn, trigger: HTMLButtonElement) => {
    detailTriggerRef.current = trigger;
    setDetailSelection({ kind: "indicator", stock, column });
  }, []);
  const openStateDetail = useCallback((stock: WatchTableStock, state: IndicatorState, trigger: HTMLButtonElement) => {
    detailTriggerRef.current = trigger;
    setDetailSelection({ kind: "state", stock, state });
  }, []);
  const closeDetail = useCallback(() => setDetailSelection(null), []);

  const removeStockMutation = useMutation({
    mutationFn: ({ securityId }: { securityId: Identifier }) => removeStockFromWatchTable(tableId, securityId),
    onMutate: () => setActionError(null),
    onSuccess: async (_result, variables) => {
      queryClient.setQueryData<WatchTableDetails>(["watch-table", tableId], (current) => {
        if (!current) return current;
        const stocks = current.stocks.filter((stock) => String(stock.security_id) !== String(variables.securityId));
        return { ...current, stocks, stock_count: stocks.length };
      });
      queryClient.setQueryData<WatchTableSummary[]>(["watch-tables"], (current) => current?.map((summary) => (
        String(summary.id) === String(tableId) ? { ...summary, stock_count: Math.max(0, summary.stock_count - 1) } : summary
      )));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-tables"] }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const updateColumnMutation = useMutation({
    mutationFn: ({ columnId, input }: { columnId: Identifier; input: UpdateColumnInput }) => updateWatchTableColumn(columnId, input),
    onMutate: () => setActionError(null),
    onSuccess: async (updated) => {
      queryClient.setQueryData<WatchTableDetails>(["watch-table", tableId], (current) => current ? {
        ...current,
        columns: current.columns.map((column) => String(column.id) === String(updated.id) ? updated : column),
      } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-table-columns", tableId] }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const reorderColumnsMutation = useMutation({
    mutationFn: (columnIds: Identifier[]) => reorderWatchTableColumns(tableId, columnIds),
    onMutate: () => setActionError(null),
    onSuccess: async (updatedColumns) => {
      queryClient.setQueryData<WatchTableDetails>(["watch-table", tableId], (current) => current ? { ...current, columns: updatedColumns } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-table-columns", tableId] }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const deleteColumnMutation = useMutation({
    mutationFn: (columnId: Identifier) => deleteWatchTableColumn(columnId),
    onMutate: () => setActionError(null),
    onSuccess: async (_result, columnId) => {
      queryClient.setQueryData<WatchTableDetails>(["watch-table", tableId], (current) => current ? {
        ...current,
        columns: current.columns.filter((column) => String(column.id) !== String(columnId)),
      } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-table-columns", tableId] }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const columns = useMemo(
    () => [...(table.columns ?? [])].sort((left, right) => (left.order ?? Number.MAX_SAFE_INTEGER) - (right.order ?? Number.MAX_SAFE_INTEGER)),
    [table.columns],
  );
  const pendingSecurityId = removeStockMutation.isPending ? removeStockMutation.variables?.securityId : undefined;
  const removeStock = removeStockMutation.mutate;
  const baseColumnDefs = useMemo(
    () => buildColumnDefs(columns, openIndicatorDetail, openStateDetail),
    [columns, openIndicatorDetail, openStateDetail],
  );
  const columnDefs = useMemo(
    () => [...baseColumnDefs, buildActionColumn({ onRemove: (securityId) => removeStock({ securityId }), pendingSecurityId })],
    [baseColumnDefs, pendingSecurityId, removeStock],
  );
  const allColumnIds = useMemo(() => columnDefs.map((column) => String(column.id)), [columnDefs]);
  const dynamicColumnIds = useMemo(() => columns.map(columnId), [columns]);
  const initialVisibility = useMemo(() => toVisibility(columns), [columns]);
  const serverView = useMemo(() => ({
    order: ["security", "price", ...dynamicColumnIds, "states", "actions"],
    visibility: { security: true, price: true, ...initialVisibility, states: true, actions: true },
  }), [dynamicColumnIds, initialVisibility]);
  const {
    columnOrder,
    columnVisibility,
    sorting,
    setColumnOrder,
    setColumnVisibility,
    setSorting,
  } = useWatchStore();

  useEffect(() => {
    setColumnOrder(serverView.order);
    setColumnVisibility(serverView.visibility);
  }, [serverView, setColumnOrder, setColumnVisibility]);

  // TanStack Table owns a mutable instance by design; React Compiler cannot safely memoize it.
  // eslint-disable-next-line react-hooks/incompatible-library
  const tableInstance = useReactTable({
    data: table.stocks ?? [],
    columns: columnDefs,
    state: {
      columnOrder: columnOrder.length > 0 ? columnOrder : allColumnIds,
      columnVisibility,
      sorting: sorting as SortingState,
    },
    onColumnOrderChange: (updater) => {
      const next = typeof updater === "function" ? updater(columnOrder) : updater;
      setColumnOrder(next);
    },
    onColumnVisibilityChange: (updater) => {
      const next = typeof updater === "function" ? updater(columnVisibility) : updater;
      setColumnVisibility(next);
    },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting as SortingState) : updater;
      setSorting(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const tableRows = tableInstance.getRowModel().rows;
  const maxCompositeFields = useMemo(() => {
    let maximum = 0;
    for (const column of columns) {
      if (String(column.view_mode ?? "").toUpperCase() !== "COMPOSITE") continue;
      for (const stock of table.stocks ?? []) {
        maximum = Math.max(maximum, getColumnFields(getIndicatorValue(stock, column)).length);
      }
    }
    return maximum;
  }, [columns, table.stocks]);
  const rowLayout = useMemo(() => watchRowLayoutContract(maxCompositeFields), [maxCompositeFields]);
  const rowHeight = rowLayout.rowHeight;
  const virtualRange = useMemo(
    () => calculateVirtualRange({
      itemCount: tableRows.length,
      scrollTop,
      viewportHeight,
      rowHeight,
      overscan: WATCH_OVERSCAN,
    }),
    [rowHeight, scrollTop, tableRows.length, viewportHeight],
  );
  const visibleRows = tableRows.slice(virtualRange.start, virtualRange.end);
  const updateViewportHeight = useCallback(() => {
    const element = viewportRef.current;
    if (element) setViewportHeight(element.clientHeight || DEFAULT_VIEWPORT_HEIGHT);
  }, []);
  const handleViewportScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  }, []);
  const handleViewportKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const nextScrollTop = virtualScrollTopForKey(
      event.key,
      event.currentTarget.scrollTop,
      event.currentTarget.clientHeight,
      virtualRange.totalHeight,
      rowHeight,
    );
    if (nextScrollTop === null) return;
    event.preventDefault();
    event.currentTarget.scrollTop = nextScrollTop;
    setScrollTop(nextScrollTop);
  }, [rowHeight, virtualRange.totalHeight]);

  useEffect(() => {
    setScrollTop(0);
    if (viewportRef.current) viewportRef.current.scrollTop = 0;
  }, [tableId]);

  useEffect(() => {
    updateViewportHeight();
    window.addEventListener("resize", updateViewportHeight);
    return () => window.removeEventListener("resize", updateViewportHeight);
  }, [updateViewportHeight]);

  function updateColumn(id: string, input: UpdateColumnInput) {
    const columnIdValue = persistentColumnId(id);
    if (columnIdValue === null) {
      setActionError("该列缺少后端 ID，无法保存修改。");
      return;
    }
    updateColumnMutation.mutate({ columnId: columnIdValue, input });
  }

  function handleMove(id: string, direction: "up" | "down") {
    const nextOrder = reorderDynamicColumns(columnOrder.length > 0 ? columnOrder : allColumnIds, id, direction, dynamicColumnIds);
    const nextIds = toColumnOrderPayload(nextOrder).column_ids;
    if (nextIds.length !== dynamicColumnIds.length || nextIds.length === 0) {
      setActionError("当前指标列还没有可持久化的顺序。");
      return;
    }
    reorderColumnsMutation.mutate(nextIds);
  }

  const pendingColumnId = updateColumnMutation.isPending
    ? updateColumnMutation.variables?.columnId
    : deleteColumnMutation.isPending
      ? deleteColumnMutation.variables
      : null;
  const anyColumnMutationPending = updateColumnMutation.isPending || deleteColumnMutation.isPending || reorderColumnsMutation.isPending;
  const managerItems: ColumnManagerItem[] = tableInstance.getAllLeafColumns().map((column) => {
    const source = columns.find((candidate, index) => columnId(candidate, index) === column.id);
    const fixed = ["security", "price", "states", "actions"].includes(column.id);
    return {
      id: column.id,
      label: String(column.columnDef.meta && "label" in column.columnDef.meta ? column.columnDef.meta.label : column.id),
      visible: column.getIsVisible(),
      movable: !fixed,
      toggleable: !fixed,
      width: source?.width,
      viewMode: source?.view_mode,
      deletable: !fixed,
      pending: anyColumnMutationPending && (reorderColumnsMutation.isPending || (pendingColumnId !== undefined && pendingColumnId !== null && String(pendingColumnId) === column.id)),
    };
  });

  return (
    <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-primary">{table.name}</p>
          <p className="mt-1 text-xs text-muted">{table.stocks.length} 支股票 · 指标由后端提供</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {columns.length === 0 ? <span className="text-[11px] text-muted">暂无指标列</span> : null}
          <button type="button" onClick={(event) => { addColumnTriggerRef.current = event.currentTarget; setActionError(null); setAddColumnOpen(true); }} className="inline-flex h-8 items-center gap-1.5 rounded-panel bg-brand px-2.5 text-xs font-medium text-white transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40">
            <span className="text-base leading-none">+</span>
            添加指标列
          </button>
          <ColumnManager
            items={managerItems}
            onToggle={(id, visible) => updateColumn(id, { visible })}
            onMove={handleMove}
            onWidthChange={(id, width) => updateColumn(id, { width })}
            onViewModeChange={(id, viewMode) => viewMode && updateColumn(id, { view_mode: viewMode as IndicatorViewMode })}
            onDelete={(id) => {
              const columnIdValue = persistentColumnId(id);
              if (columnIdValue === null) {
                setActionError("该列缺少后端 ID，无法删除。");
                return;
              }
              deleteColumnMutation.mutate(columnIdValue);
            }}
          />
        </div>
      </div>
      {actionError ? <p className="border-b border-negative/30 bg-negative/10 px-4 py-2 text-xs text-negative" role="alert">{actionError}</p> : null}
      <div
        ref={viewportRef}
        className="max-h-[min(70vh,720px)] min-h-[180px] overflow-auto focus:outline-none focus:ring-2 focus:ring-brand/40"
        onScroll={handleViewportScroll}
        onKeyDown={handleViewportKeyDown}
        tabIndex={0}
        role="region"
        aria-label={`${table.name}股票监控数据`}
        aria-describedby={`watch-table-keyboard-help-${String(tableId)}`}
      >
        <p id={`watch-table-keyboard-help-${String(tableId)}`} className="sr-only">聚焦此区域后，使用上下方向键按行移动，PageUp/PageDown 按页移动，Home/End 跳转首尾；移动后按 Tab 访问当前区域内的操作。</p>
        <table className="w-full min-w-[860px] border-collapse text-left" aria-label={`${table.name}股票监控表`} aria-rowcount={tableRows.length + 1}>
          <thead className="sticky top-0 z-10 h-10 border-b border-line bg-card/50 text-[11px] font-medium uppercase tracking-wide text-secondary">
            {tableInstance.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} aria-rowindex={1}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id} style={columnWidthStyle(columns, header.column.id)} className="whitespace-nowrap px-4 font-medium first:pl-5 last:pr-5">
                    {header.isPlaceholder ? null : <SortableHeader header={header} />}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-line/80">
            {tableRows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, tableInstance.getVisibleLeafColumns().length)}>
                  <div className="flex min-h-44 flex-col items-center justify-center px-6 py-10 text-center">
                    <p className="text-sm font-medium text-primary">还没有监控股票</p>
                    <p className="mt-1.5 text-xs text-muted">添加第一只股票后，后端行情和指标会出现在这里。</p>
                    {onAddStock ? <button type="button" onClick={(event) => onAddStock(event.currentTarget)} className="mt-4 rounded-panel bg-brand px-3 py-2 text-xs font-medium text-white hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40">添加股票</button> : null}
                  </div>
                </td>
              </tr>
            ) : null}
            {tableRows.length > 0 ? (
              <>
                {virtualRange.offsetTop > 0 ? (
                  <tr aria-hidden="true" role="presentation">
                    <td colSpan={Math.max(1, tableInstance.getVisibleLeafColumns().length)} className="p-0" style={{ height: virtualRange.offsetTop }} />
                  </tr>
                ) : null}
                {visibleRows.map((row, visibleIndex) => (
              <tr key={row.id} aria-rowindex={virtualRange.start + visibleIndex + 2} style={{ height: rowHeight }} className="align-middle transition-colors hover:bg-card/40">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} style={{ ...(columnWidthStyle(columns, cell.column.id) ?? {}), height: rowHeight }} className="whitespace-nowrap px-4 py-0 first:pl-5 last:pr-5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
                ))}
                {virtualRange.offsetBottom > 0 ? (
                  <tr aria-hidden="true" role="presentation">
                    <td colSpan={Math.max(1, tableInstance.getVisibleLeafColumns().length)} className="p-0" style={{ height: virtualRange.offsetBottom }} />
                  </tr>
                ) : null}
              </>
            ) : null}
          </tbody>
        </table>
      </div>
      {addColumnOpen ? <AddColumnDialog tableId={tableId} restoreFocusRef={addColumnTriggerRef} onClose={closeAddColumnDialog} /> : null}
      <IndicatorDetailsDrawer
        selection={detailSelection}
        onClose={closeDetail}
        restoreFocusRef={detailTriggerRef}
        onCreateNotification={onCreateNotification}
      />
    </div>
  );
}
