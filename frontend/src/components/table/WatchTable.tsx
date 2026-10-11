"use client";

import { queryKeys } from "@/lib/query-keys.mjs";

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
import { createPortal } from "react-dom";

import type {
  Identifier,
  IndicatorState,
  UpdateColumnInput,
  WatchTableColumn,
  WatchTableDetails,
  WatchTableStock,
  WatchTableSummary,
} from "@/api/types";
import {
  deleteWatchTableColumn,
  removeStockFromWatchTable,
  reorderWatchTableStocks,
  reorderWatchTableColumns,
  updateWatchTableColumn,
} from "@/api/watch";
import { getColumnTitle, getIndicatorValue, isDividendYieldColumn } from "@/components/indicators/indicator-utils";
import { IndicatorCell } from "@/components/indicators/IndicatorCell";
import { isApiError } from "@/lib/api-errors";
import { isDetailActivationKey } from "@/lib/detail-contract.mjs";
import {
  expectedCompositeFieldCount,
  filterWatchStocks,
  reorderColumnIdsForDrop,
  reorderStockIdsForDrop,
  toColumnOrderPayload,
  WATCH_COMPOSITE_LAYOUT,
  watchRowLayoutContract,
} from "@/lib/watch-contract.mjs";
import { useWatchStore } from "@/stores/watch-store";
import { useWorkspaceSettings } from "@/lib/use-workspace-settings";
import { stateToneFromLevel, StateTag } from "@/components/ui/StateTag";
import {
  IndicatorDetailsDrawer,
  type DetailNotificationHandler,
  type DetailSelection,
} from "@/components/indicators/IndicatorDetailsDrawer";

import { ColumnManager, type ColumnManagerItem } from "./ColumnManager";

const DEFAULT_COLUMN_WIDTHS = {
  security: 190,
  price: 120,
  states: 220,
  actions: 135,
  scalar: 132,
  composite: 220,
} as const;

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
  const [expanded, setExpanded] = useState(false);
  const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const overflow = states.slice(WATCH_COMPOSITE_LAYOUT.maxStateTags);

  useEffect(() => {
    if (!expanded) return;
    popoverRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setExpanded(false); triggerRef.current?.focus(); }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!popoverRef.current?.contains(event.target as Node) && !triggerRef.current?.contains(event.target as Node)) setExpanded(false);
    };
    const onScroll = (event: Event) => {
      // Scrolling inside the expanded list must not dismiss it.
      if (!popoverRef.current?.contains(event.target as Node)) setExpanded(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [expanded]);

  if (states.length === 0) return <span className="text-xs text-muted">—</span>;
  return (
    <div className="flex h-6 max-w-[260px] flex-nowrap items-center gap-1 overflow-x-auto">
      {states.slice(0, WATCH_COMPOSITE_LAYOUT.maxStateTags).map((state) => (
        <button
          key={`${state.state_id}-${state.parameter_key ?? ""}`}
          type="button"
          onClick={(event) => onOpen?.(state, event.currentTarget)}
          className="rounded-full focus:outline-none focus:ring-2 focus:ring-brand/40"
          aria-label={`查看状态${state.title}详情`}
        >
          <StateTag compact tone={stateToneFromLevel(state.level ?? state.severity)}>{state.title}</StateTag>
        </button>
      ))}
      {overflow.length ? (
        <button ref={triggerRef} type="button" aria-expanded={expanded} aria-label={`展开其余${overflow.length}个状态`} onClick={() => {
          if (expanded) { setExpanded(false); return; }
          const bounds = triggerRef.current?.getBoundingClientRect();
          if (!bounds) return;
          setAnchor({ left: Math.max(8, Math.min(bounds.left, window.innerWidth - 290)), top: Math.max(8, Math.min(bounds.bottom + 6, window.innerHeight - 120)) });
          setExpanded(true);
        }} className="shrink-0 rounded px-1 text-xs text-brand hover:bg-brand/10 focus:outline-none focus:ring-2 focus:ring-brand/40">+{overflow.length}</button>
      ) : null}
      {expanded && anchor ? createPortal(
        <div ref={popoverRef} role="group" aria-label="更多股票状态" className="fixed z-40 max-h-[min(60vh,20rem)] w-72 space-y-1 overflow-y-auto rounded-panel border border-line bg-panel p-2 shadow-panel" style={anchor}>
          {overflow.map((state) => (
            <button key={`${state.state_id}-${state.parameter_key ?? ""}`} type="button" className="block w-full rounded px-2 py-2 text-left text-xs text-secondary hover:bg-card" onClick={(event) => {
              onOpen?.(state, triggerRef.current ?? event.currentTarget);
              setExpanded(false);
            }}>{state.title}{state.parameters && Object.keys(state.parameters).length ? <span className="mt-1 block text-[10px] text-muted">{Object.entries(state.parameters).map(([key, value]) => `${key}=${value}`).join("、")}</span> : null}</button>
          ))}
        </div>, document.body
      ) : null}
    </div>
  );
}

function buildColumnDefs(
  columns: WatchTableColumn[],
  onOpenIndicator: (stock: WatchTableStock, column: WatchTableColumn, trigger: HTMLButtonElement) => void,
  onOpenState: (stock: WatchTableStock, state: IndicatorState, trigger: HTMLButtonElement) => void,
  showDeltas: boolean,
  isBootstrapping: (stock: WatchTableStock) => boolean,
): ColumnDef<WatchTableStock, unknown>[] {
  const dynamicColumns = columns.map((column, index) => {
    const id = columnId(column, index);
    return {
      id,
      accessorFn: (stock: WatchTableStock) => isBootstrapping(stock) ? undefined : getIndicatorValue(stock, column),
      header: getColumnTitle(column),
      sortingFn: (rowA, rowB, sortingColumnId) => {
        const valueA = sortValue(rowA.getValue(sortingColumnId));
        const valueB = sortValue(rowB.getValue(sortingColumnId));
        if (typeof valueA === "number" && typeof valueB === "number") return valueA - valueB;
        return String(valueA).localeCompare(String(valueB), "zh-CN", { numeric: true });
      },
      cell: ({ row }) => {
        if (isBootstrapping(row.original)) return <span className="text-xs text-muted">—</span>;
        return (
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
            <IndicatorCell
              mode={isDividendYieldColumn(column) ? "NUMBER" : column.view_mode === "DELTA" && !showDeltas ? "NUMBER" : column.view_mode}
              value={getIndicatorValue(row.original, column)}
              states={row.original.column_states?.[id] ?? []}
              percentage={isDividendYieldColumn(column)}
            />
          </button>
        );
      },
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
        const preparing = isBootstrapping(stock);
        return (
          <div className="w-full min-w-0 overflow-hidden whitespace-nowrap">
            <div className="flex min-w-0 items-center gap-2">
              <p className="min-w-0 flex-1 truncate font-medium text-primary" title={stock.name ?? stock.security?.name ?? "未命名证券"}>{stock.name ?? stock.security?.name ?? "未命名证券"}</p>
              {preparing ? <span className="shrink-0 rounded-full border border-brand/25 bg-brand/10 px-1.5 py-0.5 text-[10px] font-medium text-brand">数据准备中</span> : null}
            </div>
            <p className="mt-1 truncate font-mono text-[11px] text-muted">{stock.symbol}</p>
          </div>
        );
      },
      meta: { label: "股票", movable: false, toggleable: false },
    },
    {
      id: "price",
      accessorFn: (stock) => isBootstrapping(stock) ? undefined : stock.price,
      header: "当前价",
      sortingFn: (rowA, rowB) => compareRows(rowA.original, rowB.original, "price"),
      cell: ({ row }) => {
        if (isBootstrapping(row.original)) return <span className="text-xs text-muted">—</span>;
        const quote = row.original.price;
        const kind = typeof quote === "object" && quote !== null ? quote.timestamp_kind : undefined;
        const stale = typeof quote === "object" && quote !== null && quote.observation_stale === true;
        const observed = typeof quote === "object" && quote !== null ? quote.timestamp : undefined;
        return (
          <div title={kind === "OBSERVED_AT" ? "数据采集时间：" + (observed ?? "未知") + "；不等于交易所原始更新时间" : "最近未复权日线收盘价"}>
            <IndicatorCell mode={showDeltas ? "DELTA" : "NUMBER"} value={quote} />
            {kind === "OBSERVED_AT" ? (
              <span className={"text-[10px] " + (stale ? "text-warning" : "text-muted")}>
                {stale ? "采集已超20分钟" : "行情采集值"}
              </span>
            ) : kind === "DAILY_BAR_DATE" ? (
              <span className="text-[10px] text-muted">日线收盘</span>
            ) : null}
          </div>
        );
      },
      meta: { label: "当前价", movable: false, toggleable: false },
    },
    ...dynamicColumns,
    {
      id: "states",
      accessorFn: (stock) => isBootstrapping(stock) ? "" : stock.states?.map((state) => state.title).join(" ") ?? "",
      header: "状态",
      cell: ({ row }) => isBootstrapping(row.original)
        ? <StateTag compact tone="neutral">准备中</StateTag>
        : <StateList states={row.original.states ?? []} onOpen={(state, trigger) => onOpenState(row.original, state, trigger)} />,
      meta: { label: "状态", movable: false, toggleable: false },
    },
  ];
}

function buildActionColumn({
  onRemove,
  pendingSecurityId,
  onMove,
  canMove = false,
  stockOrder = [],
}: {
  onMove?: (securityId: Identifier, offset: -1 | 1) => void;
  canMove?: boolean;
  stockOrder?: Identifier[];
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
      const index = stockOrder.findIndex((id) => String(id) === String(securityId));
      return (
        <div className="flex items-center gap-1">
        <button type="button" title="上移股票" aria-label={`上移${row.original.name ?? row.original.symbol}`} disabled={!canMove || index <= 0} onClick={() => onMove?.(securityId, -1)} className="text-xs text-muted disabled:opacity-30">↑</button>
        <button type="button" title="下移股票" aria-label={`下移${row.original.name ?? row.original.symbol}`} disabled={!canMove || index < 0 || index === stockOrder.length - 1} onClick={() => onMove?.(securityId, 1)} className="text-xs text-muted disabled:opacity-30">↓</button>
        <button
          type="button"
          disabled={pending}
          onClick={() => onRemove(securityId)}
          className="rounded px-2 py-1 text-xs text-muted transition hover:bg-negative/10 hover:text-negative disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`删除${row.original.name ?? row.original.symbol}`}
        >
          {pending ? "删除中…" : "移除"}
        </button>
        </div>
      );
    },
    meta: { label: "操作", movable: false, toggleable: false },
  } satisfies ColumnDef<WatchTableStock, unknown>;
}

function toVisibility(columns: WatchTableColumn[]): VisibilityState {
  return Object.fromEntries(columns.map((column, index) => [columnId(column, index), column.hidden !== true && column.visible !== false]));
}

function columnPixelWidth(columns: WatchTableColumn[], id: string): number {
  const source = columns.find((column, index) => columnId(column, index) === id);
  if (source?.width) return source.width;
  if (id === "security") return DEFAULT_COLUMN_WIDTHS.security;
  if (id === "price") return DEFAULT_COLUMN_WIDTHS.price;
  if (id === "states") return DEFAULT_COLUMN_WIDTHS.states;
  if (id === "actions") return DEFAULT_COLUMN_WIDTHS.actions;
  return String(source?.view_mode ?? "").toUpperCase() === "COMPOSITE"
    ? DEFAULT_COLUMN_WIDTHS.composite
    : DEFAULT_COLUMN_WIDTHS.scalar;
}

function columnWidthStyle(columns: WatchTableColumn[], id: string): React.CSSProperties {
  const width = columnPixelWidth(columns, id);
  return { width, minWidth: width, maxWidth: width };
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
  bootstrappingStockIds,
  onAddStock,
  onCreateNotification,
}: {
  table: WatchTableDetails;
  bootstrappingStockIds?: ReadonlySet<string>;
  onAddStock?: (trigger?: HTMLButtonElement) => void;
  onCreateNotification?: DetailNotificationHandler;
}) {
  const queryClient = useQueryClient();
  const { settings: workspaceSettings } = useWorkspaceSettings();
  const [actionError, setActionError] = useState<string | null>(null);
  const [detailSelection, setDetailSelection] = useState<DetailSelection | null>(null);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [draggedStockId, setDraggedStockId] = useState<Identifier | null>(null);
  const tableViewportRef = useRef<HTMLDivElement>(null);
  const [fixedHeader, setFixedHeader] = useState<{ left: number; width: number; scrollLeft: number } | null>(null);
  const [filterQuery, setFilterQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("ALL");
  const tableId = table.id;

  const openIndicatorDetail = useCallback((stock: WatchTableStock, column: WatchTableColumn, trigger: HTMLButtonElement) => {
    detailTriggerRef.current = trigger;
    setDetailSelection({ kind: "indicator", stock, column, adjust_type: table.adjust_type });
  }, [table.adjust_type]);
  const openStateDetail = useCallback((stock: WatchTableStock, state: IndicatorState, trigger: HTMLButtonElement) => {
    detailTriggerRef.current = trigger;
    setDetailSelection({ kind: "state", stock, state });
  }, []);
  const closeDetail = useCallback(() => setDetailSelection(null), []);
  const isBootstrapping = useCallback((stock: WatchTableStock) => {
    const securityId = stock.security_id ?? stock.security?.security_id ?? stock.security?.id;
    return securityId !== undefined
      && securityId !== null
      && Boolean(bootstrappingStockIds?.has(String(securityId)));
  }, [bootstrappingStockIds]);

  const removeStockMutation = useMutation({
    mutationFn: ({ securityId }: { securityId: Identifier }) => removeStockFromWatchTable(tableId, securityId),
    onMutate: () => setActionError(null),
    onSuccess: async (_result, variables) => {
      queryClient.setQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) }, (current) => {
        if (!current) return current;
        const stocks = current.stocks.filter((stock) => String(stock.security_id) !== String(variables.securityId));
        return { ...current, stocks, stock_count: stocks.length };
      });
      queryClient.setQueryData<WatchTableSummary[]>(queryKeys.watchTables(), (current) => current?.map((summary) => (
        String(summary.id) === String(tableId) ? { ...summary, stock_count: Math.max(0, summary.stock_count - 1) } : summary
      )));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.watchTablePrefix(tableId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.watchTables() }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const reorderStockMutation = useMutation({
    mutationFn: (securityIds: Identifier[]) => reorderWatchTableStocks(tableId, securityIds),
    onMutate: async (securityIds) => {
      setActionError(null);
      await queryClient.cancelQueries({ queryKey: queryKeys.watchTablePrefix(tableId) });
      const previous = queryClient.getQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) });
      queryClient.setQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) }, (current) => {
        if (!current) return current;
        const byId = new Map(current.stocks.map((stock) => [String(stock.security_id), stock]));
        if (securityIds.length !== current.stocks.length || securityIds.some((id) => !byId.has(String(id)))) return current;
        return { ...current, stocks: securityIds.map((id) => byId.get(String(id))!) };
      });
      return { previous };
    },
    onError: (error, _ids, context) => {
      context?.previous.forEach(([key, value]) => queryClient.setQueryData<WatchTableDetails>(key, value));
      setActionError(mutationErrorMessage(error));
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchTablePrefix(tableId) });
    },
  });

  const updateColumnMutation = useMutation({
    mutationFn: ({ columnId, input }: { columnId: Identifier; input: UpdateColumnInput }) => updateWatchTableColumn(columnId, input),
    onMutate: () => setActionError(null),
    onSuccess: async (updated) => {
      queryClient.setQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) }, (current) => current ? {
        ...current,
        columns: current.columns.map((column) => String(column.id) === String(updated.id) ? updated : column),
      } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.watchTablePrefix(tableId) }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const reorderColumnsMutation = useMutation({
    mutationFn: (columnIds: Identifier[]) => reorderWatchTableColumns(tableId, columnIds),
    onMutate: () => setActionError(null),
    onSuccess: async (updatedColumns) => {
      queryClient.setQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) }, (current) => current ? { ...current, columns: updatedColumns } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.watchTablePrefix(tableId) }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const deleteColumnMutation = useMutation({
    mutationFn: (columnId: Identifier) => deleteWatchTableColumn(columnId),
    onMutate: () => setActionError(null),
    onSuccess: async (_result, columnId) => {
      queryClient.setQueriesData<WatchTableDetails>({ queryKey: queryKeys.watchTablePrefix(tableId) }, (current) => current ? {
        ...current,
        columns: current.columns.filter((column) => String(column.id) !== String(columnId)),
      } : current);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.watchTablePrefix(tableId) }),
      ]);
    },
    onError: (error) => setActionError(mutationErrorMessage(error)),
  });

  const columns = useMemo(
    () => [...(table.columns ?? [])].sort((left, right) => (left.order ?? Number.MAX_SAFE_INTEGER) - (right.order ?? Number.MAX_SAFE_INTEGER)),
    [table.columns],
  );
  const {
    columnOrder,
    columnVisibility,
    sorting,
    setColumnOrder,
    setColumnVisibility,
    setSorting,
  } = useWatchStore();
  const pendingSecurityId = removeStockMutation.isPending ? removeStockMutation.variables?.securityId : undefined;
  const removeStock = removeStockMutation.mutate;
  const baseColumnDefs = useMemo(
    () => buildColumnDefs(columns, openIndicatorDetail, openStateDetail, workspaceSettings.showDeltas, isBootstrapping),
    [columns, isBootstrapping, openIndicatorDetail, openStateDetail, workspaceSettings.showDeltas],
  );
  const columnDefs = useMemo(
    () => [...baseColumnDefs, buildActionColumn({ onRemove: (securityId) => removeStock({ securityId }), pendingSecurityId, onMove: moveStockByOffset, canMove: !filterQuery.trim() && stateFilter === "ALL" && sorting.length === 0 && !reorderStockMutation.isPending, stockOrder: table.stocks.map((stock) => stock.security_id).filter((id): id is Identifier => id != null) })],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [baseColumnDefs, pendingSecurityId, removeStock, filterQuery, stateFilter, sorting, reorderStockMutation.isPending, table.stocks],
  );
  const allColumnIds = useMemo(() => columnDefs.map((column) => String(column.id)), [columnDefs]);
  const dynamicColumnIds = useMemo(() => columns.map(columnId), [columns]);
  const initialVisibility = useMemo(() => toVisibility(columns), [columns]);
  const serverView = useMemo(() => ({
    order: ["security", "price", ...dynamicColumnIds, "states", "actions"],
    visibility: { security: true, price: true, ...initialVisibility, states: workspaceSettings.showStates, actions: true },
  }), [dynamicColumnIds, initialVisibility, workspaceSettings.showStates]);

  useEffect(() => {
    setColumnOrder(serverView.order);
    setColumnVisibility(serverView.visibility);
  }, [serverView, setColumnOrder, setColumnVisibility]);

  const stateFilterOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const stock of table.stocks ?? []) {
      for (const state of stock.states ?? []) {
        const code = String(state.state_code ?? state.state_id ?? "").trim();
        if (code && !options.has(code)) options.set(code, state.title ?? code);
      }
    }
    return [...options.entries()].sort((left, right) => left[1].localeCompare(right[1], "zh-CN"));
  }, [table.stocks]);
  const filteredStocks = useMemo(
    () => filterWatchStocks(table.stocks ?? [], { query: filterQuery, state: stateFilter }) as WatchTableStock[],
    [filterQuery, stateFilter, table.stocks],
  );

  // TanStack Table owns a mutable instance by design; React Compiler cannot safely memoize it.
  // eslint-disable-next-line react-hooks/incompatible-library
  const tableInstance = useReactTable({
    data: filteredStocks,
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
  useEffect(() => {
    const viewport = tableViewportRef.current;
    if (!viewport) return;
    const update = () => {
      const bounds = viewport.getBoundingClientRect();
      const height = workspaceSettings.density === "comfortable" ? 48 : 40;
      const shown = bounds.top < 0 && bounds.bottom > height && bounds.right > 0 && bounds.left < window.innerWidth;
      const next = shown ? { left: bounds.left, width: bounds.width, scrollLeft: viewport.scrollLeft } : null;
      setFixedHeader((current) => current?.left === next?.left && current?.width === next?.width && current?.scrollLeft === next?.scrollLeft ? current : next);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    viewport.addEventListener("scroll", update, { passive: true });
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
    };
  }, [workspaceSettings.density]);
  const visibleLeafColumns = tableInstance.getVisibleLeafColumns();
  const tablePixelWidth = visibleLeafColumns.reduce(
    (total, column) => total + columnPixelWidth(columns, column.id),
    0,
  );
  const layoutColumns = useMemo(
    () => columns.map((column, index) => {
      const id = columnId(column, index);
      const visible = columnVisibility[id] ?? (column.hidden !== true && column.visible !== false);
      return { ...column, visible, hidden: !visible };
    }),
    [columnVisibility, columns],
  );
  const maxCompositeFields = useMemo(
    () => expectedCompositeFieldCount(layoutColumns),
    [layoutColumns],
  );
  const comfortableDensity = workspaceSettings.density === "comfortable";
  const rowLayout = useMemo(
    () => watchRowLayoutContract(maxCompositeFields, workspaceSettings.density),
    [maxCompositeFields, workspaceSettings.density],
  );
  const rowHeight = rowLayout.rowHeight;
  const canReorderStocks = !filterQuery.trim() && stateFilter === "ALL" && sorting.length === 0 && !reorderStockMutation.isPending;
  const stockOrder = table.stocks.map((stock) => stock.security_id).filter((id): id is Identifier => id !== undefined && id !== null);
  function moveStock(sourceId: Identifier, targetId: Identifier) {
    if (!canReorderStocks || stockOrder.length !== table.stocks.length) return;
    const next = reorderStockIdsForDrop(stockOrder, sourceId, targetId);
    if (next) reorderStockMutation.mutate(next);
  }
  function moveStockByOffset(sourceId: Identifier, offset: -1 | 1) {
    const index = stockOrder.findIndex((id) => String(id) === String(sourceId));
    if (index < 0 || index + offset < 0 || index + offset >= stockOrder.length) return;
    moveStock(sourceId, stockOrder[index + offset]);
  }

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

  function handleReorder(sourceId: string, targetId: string, position: "before" | "after") {
    const baseOrder = columnOrder.length > 0 ? columnOrder : allColumnIds;
    const currentDynamicOrder = [
      ...baseOrder.filter((id) => dynamicColumnIds.includes(id)),
      ...dynamicColumnIds.filter((id) => !baseOrder.includes(id)),
    ];
    const nextDynamicOrder = reorderColumnIdsForDrop(currentDynamicOrder, sourceId, targetId, position);
    const nextIds = toColumnOrderPayload(nextDynamicOrder).column_ids;
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
    const dividendYield = source ? isDividendYieldColumn(source) : false;
    return {
      id: column.id,
      label: String(column.columnDef.meta && "label" in column.columnDef.meta ? column.columnDef.meta.label : column.id),
      visible: column.getIsVisible(),
      movable: !fixed,
      toggleable: !fixed,
      width: source?.width,
      viewMode: dividendYield ? "NUMBER" : source?.view_mode,
      indicatorType: source?.indicator_type,
      parameters: source?.parameters,
      stateCode: source?.state_code,
      viewModeLocked: dividendYield,
      deletable: !fixed,
      pending: anyColumnMutationPending && (reorderColumnsMutation.isPending || (pendingColumnId !== undefined && pendingColumnId !== null && String(pendingColumnId) === column.id)),
    };
  });

  return (
    <div data-density={workspaceSettings.density} className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
      <div className={`flex flex-wrap items-center justify-between gap-3 border-b border-line ${comfortableDensity ? "px-5 py-4" : "px-4 py-3"}`}>
        <div>
          <p className="text-sm font-semibold text-primary">{table.name}</p>
          <p className="mt-1 text-xs text-muted">{filteredStocks.length === table.stocks.length ? `${table.stocks.length} 支股票` : `${filteredStocks.length} / ${table.stocks.length} 支股票`}</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <input
            type="search"
            value={filterQuery}
            onChange={(event) => setFilterQuery(event.target.value)}
            placeholder="筛选股票名称/代码"
            aria-label="筛选股票名称或代码"
            className="h-8 w-44 rounded-panel border border-line bg-card px-2.5 text-xs text-primary outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
          />
          <select
            value={stateFilter}
            onChange={(event) => setStateFilter(event.target.value)}
            aria-label="筛选股票状态"
            className="h-8 max-w-48 rounded-panel border border-line bg-card px-2 text-xs text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
          >
            <option value="ALL">全部状态</option>
            <option value="HAS_STATE">有活跃状态</option>
            {stateFilter !== "ALL"
              && stateFilter !== "HAS_STATE"
              && !stateFilterOptions.some(([code]) => code === stateFilter)
              ? <option value={stateFilter}>{stateFilter}（当前无匹配）</option>
              : null}
            {stateFilterOptions.map(([code, title]) => <option key={code} value={code}>{title}</option>)}
          </select>
          {(filterQuery || stateFilter !== "ALL") ? (
            <button type="button" onClick={() => { setFilterQuery(""); setStateFilter("ALL"); }} className="h-8 rounded-panel border border-line bg-card px-2.5 text-xs text-secondary hover:text-primary">清除筛选</button>
          ) : null}
          {columns.length === 0 ? <span className="text-[11px] text-muted">暂无指标列</span> : null}
          <ColumnManager
            tableId={tableId}
            items={managerItems}
            onToggle={(id, visible) => updateColumn(id, { visible })}
            onMove={handleMove}
            onReorder={handleReorder}
            onWidthChange={(id, width) => updateColumn(id, { width })}
            onViewModeChange={async (id, input) => {
              const columnIdValue = persistentColumnId(id);
              if (columnIdValue === null) throw new Error("该列缺少后端 ID。");
              await updateColumnMutation.mutateAsync({ columnId: columnIdValue, input });
            }}
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
      <p className="border-b border-line px-4 py-2 text-[11px] text-muted">默认顺序下可拖动股票行或使用操作列的 ↑↓ 按钮调整顺序；筛选或按列排序时暂停手动排序。</p>
      {actionError ? <p className="border-b border-negative/30 bg-negative/10 px-4 py-2 text-xs text-negative" role="alert">{actionError}</p> : null}
      <div ref={tableViewportRef} className="overflow-x-auto overflow-y-visible" role="region" aria-label={`${table.name}股票监控数据`} tabIndex={0}>
        <table
          className="table-fixed border-collapse text-left"
          style={{ width: tablePixelWidth, minWidth: "100%" }}
          aria-label={`${table.name}股票监控表`}
          aria-rowcount={tableRows.length + 1}
        >
          <colgroup>
            {visibleLeafColumns.map((column) => <col key={column.id} style={columnWidthStyle(columns, column.id)} />)}
          </colgroup>
          <thead className={`border-b border-line bg-card/50 text-[11px] font-medium uppercase tracking-wide text-secondary ${comfortableDensity ? "h-12" : "h-10"}`}>
            {tableInstance.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} aria-rowindex={1}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id} style={columnWidthStyle(columns, header.column.id)} className={`whitespace-nowrap font-medium ${comfortableDensity ? "px-5 first:pl-6 last:pr-6" : "px-4 first:pl-5 last:pr-5"}`}>
                    {header.isPlaceholder ? null : <SortableHeader header={header} />}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-line/80">
            {tableRows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, visibleLeafColumns.length)}>
                  <div className="flex min-h-44 flex-col items-center justify-center px-6 py-10 text-center">
                    <p className="text-sm font-medium text-primary">{table.stocks.length === 0 ? "还没有监控股票" : "没有符合筛选条件的股票"}</p>
                    <p className="mt-1.5 text-xs text-muted">{table.stocks.length === 0 ? "添加第一只股票后，后端行情和指标会出现在这里。" : "调整名称、代码或状态筛选条件后重试。"}</p>
                    {table.stocks.length === 0 && onAddStock ? <button type="button" onClick={(event) => onAddStock(event.currentTarget)} className="mt-4 rounded-panel bg-brand px-3 py-2 text-xs font-medium text-white hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40">添加股票</button> : null}
                  </div>
                </td>
              </tr>
            ) : null}
            {tableRows.length > 0 ? (
              <>
                {tableRows.map((row, visibleIndex) => (
              <tr key={row.id} aria-rowindex={visibleIndex + 2} style={{ height: rowHeight }} draggable={canReorderStocks} onDragStart={(event) => { if (!canReorderStocks || row.original.security_id == null) { event.preventDefault(); return; } setDraggedStockId(row.original.security_id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", String(row.original.security_id)); }} onDragOver={(event) => { if (canReorderStocks && draggedStockId !== null) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); if (draggedStockId !== null && row.original.security_id != null) moveStock(draggedStockId, row.original.security_id); setDraggedStockId(null); }} onDragEnd={() => setDraggedStockId(null)} className={`align-middle transition-colors hover:bg-card/40 ${canReorderStocks ? "cursor-grab" : ""} ${draggedStockId !== null && String(draggedStockId) === String(row.original.security_id) ? "opacity-50" : ""}`} >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} style={{ ...(columnWidthStyle(columns, cell.column.id) ?? {}), height: rowHeight }} className={`whitespace-nowrap py-0 ${comfortableDensity ? "px-5 first:pl-6 last:pr-6" : "px-4 first:pl-5 last:pr-5"}`}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
                ))}
              </>
            ) : null}
          </tbody>
        </table>
      </div>
      {fixedHeader ? (
        <div className="pointer-events-auto fixed top-0 z-30 overflow-hidden border-b border-line bg-panel shadow-panel" style={{ left: fixedHeader.left, width: fixedHeader.width, height: comfortableDensity ? 48 : 40 }}>
          <table className="table-fixed border-collapse text-left" style={{ width: tablePixelWidth, minWidth: "100%", transform: `translateX(-${fixedHeader.scrollLeft}px)` }} aria-label={`${table.name}固定表头`}>
            <colgroup>{visibleLeafColumns.map((column) => <col key={column.id} style={columnWidthStyle(columns, column.id)} />)}</colgroup>
            <thead className={`text-[11px] font-medium uppercase tracking-wide text-secondary ${comfortableDensity ? "h-12" : "h-10"}`}>
              {tableInstance.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => <th key={header.id} style={columnWidthStyle(columns, header.column.id)} className={`whitespace-nowrap font-medium ${comfortableDensity ? "px-5 first:pl-6 last:pr-6" : "px-4 first:pl-5 last:pr-5"}`}>{header.isPlaceholder ? null : <SortableHeader header={header} />}</th>)}
                </tr>
              ))}
            </thead>
          </table>
        </div>
      ) : null}
      <IndicatorDetailsDrawer
        selection={detailSelection}
        onClose={closeDetail}
        restoreFocusRef={detailTriggerRef}
        onCreateNotification={onCreateNotification}
      />
    </div>
  );
}
