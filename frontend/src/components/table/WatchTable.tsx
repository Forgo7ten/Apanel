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
import { useEffect, useMemo } from "react";

import type { IndicatorState, WatchTableColumn, WatchTableDetails, WatchTableStock } from "@/api/types";
import { IndicatorCell } from "@/components/indicators/IndicatorCell";
import { stateToneFromLevel, StateTag } from "@/components/ui/StateTag";
import { useWatchStore } from "@/stores/watch-store";

import { getColumnTitle, getIndicatorValue } from "@/components/indicators/indicator-utils";
import { ColumnManager, type ColumnManagerItem } from "./ColumnManager";

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

  if (!canSort) {
    return <>{label}</>;
  }

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

function StateList({ states }: { states: IndicatorState[] }) {
  if (states.length === 0) {
    return <span className="text-xs text-muted">—</span>;
  }

  return (
    <div className="flex max-w-[260px] flex-wrap gap-1">
      {states.slice(0, 3).map((state) => (
        <StateTag key={state.state_id} tone={stateToneFromLevel(state.level ?? state.severity)}>
          {state.title}
        </StateTag>
      ))}
      {states.length > 3 ? <span className="self-center text-[11px] text-muted">+{states.length - 3}</span> : null}
    </div>
  );
}

function buildColumnDefs(columns: WatchTableColumn[]): ColumnDef<WatchTableStock, unknown>[] {
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
        <IndicatorCell mode={column.view_mode} value={getIndicatorValue(row.original, column)} states={row.original.states} />
      ),
      meta: { label: getColumnTitle(column), movable: true },
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
          <div className="min-w-[150px]">
            <p className="font-medium text-primary">{stock.name ?? stock.security?.name ?? "未命名证券"}</p>
            <p className="mt-1 font-mono text-[11px] text-muted">{stock.symbol}</p>
          </div>
        );
      },
      meta: { label: "股票", movable: false },
    },
    {
      id: "price",
      accessorFn: (stock) => stock.price,
      header: "当前价",
      sortingFn: (rowA, rowB) => compareRows(rowA.original, rowB.original, "price"),
      cell: ({ row }) => <IndicatorCell mode="DELTA" value={row.original.price} />,
      meta: { label: "当前价", movable: false },
    },
    ...dynamicColumns,
    {
      id: "states",
      accessorFn: (stock) => stock.states?.map((state) => state.title).join(" ") ?? "",
      header: "状态",
      cell: ({ row }) => <StateList states={row.original.states ?? []} />,
      meta: { label: "状态", movable: false },
    },
  ];
}

function toVisibility(columns: WatchTableColumn[]): VisibilityState {
  return Object.fromEntries(
    columns.map((column, index) => [columnId(column, index), column.hidden !== true && column.visible !== false]),
  );
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
  return ["security", "price", ...nextDynamic, "states"];
}

export function WatchTable({ table }: { table: WatchTableDetails }) {
  const columns = useMemo(
    () => [...(table.columns ?? [])].sort((left, right) => (left.order ?? Number.MAX_SAFE_INTEGER) - (right.order ?? Number.MAX_SAFE_INTEGER)),
    [table.columns],
  );
  const columnDefs = useMemo(() => buildColumnDefs(columns), [columns]);
  const allColumnIds = useMemo(() => columnDefs.map((column) => String(column.id)), [columnDefs]);
  const dynamicColumnIds = useMemo(() => columns.map(columnId), [columns]);
  const initialVisibility = useMemo(() => toVisibility(columns), [columns]);
  const {
    columnOrder,
    columnVisibility,
    sorting,
    setColumnOrder,
    setColumnVisibility,
    setSorting,
  } = useWatchStore();

  useEffect(() => {
    const currentOrder = columnOrder.filter((id) => allColumnIds.includes(id));
    const missing = allColumnIds.filter((id) => !currentOrder.includes(id));
    const nextOrder = [...currentOrder, ...missing];
    if (nextOrder.join("|") !== columnOrder.join("|")) {
      setColumnOrder(nextOrder);
    }
  }, [allColumnIds, columnOrder, setColumnOrder]);

  useEffect(() => {
    if (Object.keys(columnVisibility).length === 0) {
      setColumnVisibility(initialVisibility);
    }
  }, [columnVisibility, initialVisibility, setColumnVisibility]);

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

  const managerItems: ColumnManagerItem[] = tableInstance.getAllLeafColumns().map((column) => ({
    id: column.id,
    label: String(column.columnDef.meta && "label" in column.columnDef.meta ? column.columnDef.meta.label : column.id),
    visible: column.getIsVisible(),
    movable: column.id !== "security" && column.id !== "price" && column.id !== "states",
  }));

  function handleMove(id: string, direction: "up" | "down") {
    setColumnOrder(reorderDynamicColumns(columnOrder.length > 0 ? columnOrder : allColumnIds, id, direction, dynamicColumnIds));
  }

  return (
    <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-primary">{table.name}</p>
          <p className="mt-1 text-xs text-muted">{table.stocks.length} 支股票 · 指标由后端提供</p>
        </div>
        <ColumnManager
          items={managerItems}
          onToggle={(id, visible) => tableInstance.getColumn(id)?.toggleVisibility(visible)}
          onMove={handleMove}
        />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse text-left" aria-label={`${table.name}股票监控表`}>
          <thead className="h-10 border-b border-line bg-card/50 text-[11px] font-medium uppercase tracking-wide text-secondary">
            {tableInstance.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id} className="whitespace-nowrap px-4 font-medium first:pl-5 last:pr-5">
                    {header.isPlaceholder ? null : <SortableHeader header={header} />}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-line/80">
            {tableInstance.getRowModel().rows.map((row) => (
              <tr key={row.id} className="align-middle transition-colors hover:bg-card/40">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-3.5 first:pl-5 last:pr-5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
