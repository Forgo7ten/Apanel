"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Identifier } from "@/api/types";
import { createWatchTable, deleteWatchTable, getWatchTable, getWatchTables } from "@/api/watch";
import { AddStockDialog } from "@/components/table/AddStockDialog";
import { WatchTable } from "@/components/table/WatchTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState, QueryErrorState } from "@/components/ui/QueryState";
import { StateTag } from "@/components/ui/StateTag";
import { isApiError } from "@/lib/api-errors";
import { isCurrentWatchDetail, watchDetailViewState } from "@/lib/watch-contract.mjs";
import { useWatchStore } from "@/stores/watch-store";

function sameId(left: Identifier | null, right: Identifier | null): boolean {
  return left !== null && right !== null && String(left) === String(right);
}

export default function WatchPage() {
  const queryClient = useQueryClient();
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const addStockTriggerRef = useRef<HTMLButtonElement | null>(null);
  const closeAddStockDialog = useCallback(() => setAddDialogOpen(false), []);
  const { selectedTableId, setSelectedTableId, resetTableView } = useWatchStore();
  const tablesQuery = useQuery({ queryKey: ["watch-tables"], queryFn: getWatchTables });
  const tables = useMemo(() => tablesQuery.data ?? [], [tablesQuery.data]);
  const selectedTable = tables.find((table) => sameId(table.id, selectedTableId)) ?? tables[0];
  const activeTableId = selectedTable?.id ?? null;
  const detailsQuery = useQuery({
    queryKey: ["watch-table", activeTableId],
    queryFn: () => getWatchTable(activeTableId as Identifier),
    enabled: activeTableId !== null,
    placeholderData: (previous) => previous,
  });
  const currentDetail = detailsQuery.data && isCurrentWatchDetail(detailsQuery.data, activeTableId)
    ? detailsQuery.data
    : undefined;
  const detailState = watchDetailViewState({
    isPending: detailsQuery.isPending || (detailsQuery.isFetching && !currentDetail),
    isFetching: detailsQuery.isFetching,
    isError: detailsQuery.isError,
    hasData: Boolean(currentDetail),
  });
  const createMutation = useMutation({
    mutationFn: () => {
      const name = createName.trim();
      if (!name) throw new Error("请输入监控表名称。");
      return createWatchTable({ name });
    },
    onSuccess: async (table) => {
      setCreateName("");
      await queryClient.invalidateQueries({ queryKey: ["watch-tables"] });
      setSelectedTableId(table.id);
      resetTableView();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (tableId: Identifier) => deleteWatchTable(tableId),
    onSuccess: async (_result, tableId) => {
      queryClient.setQueryData<typeof tables>(["watch-tables"], (current) => current?.filter((table) => !sameId(table.id, tableId)));
      queryClient.removeQueries({ queryKey: ["watch-table", tableId] });
      setSelectedTableId(null);
      resetTableView();
      await queryClient.invalidateQueries({ queryKey: ["watch-tables"] });
    },
  });

  useEffect(() => {
    if (tables.length === 0) {
      if (selectedTableId !== null) {
        setSelectedTableId(null);
        resetTableView();
      }
      return;
    }

    if (!tables.some((table) => sameId(table.id, selectedTableId))) {
      setSelectedTableId(tables[0].id);
      resetTableView();
    }
  }, [resetTableView, selectedTableId, setSelectedTableId, tables]);

  function selectTable(id: Identifier) {
    if (sameId(id, selectedTableId)) return;
    setAddDialogOpen(false);
    setSelectedTableId(id);
    resetTableView();
  }

  const createError = createMutation.error;
  const createErrorMessage = isApiError(createError) && createError.status === 404
    ? "后端尚未提供创建监控表接口。"
    : createError instanceof Error
      ? createError.message
      : null;
  const deleteError = deleteMutation.error;
  const deleteErrorMessage = isApiError(deleteError) && deleteError.status === 404
    ? "当前监控表不存在或无权限，请刷新后重试。"
    : deleteError instanceof Error
      ? deleteError.message
      : null;

  return (
    <div className="space-y-5">
      <section className="flex flex-col justify-between gap-4 xl:flex-row xl:items-end">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-brand">Watch workspace</p>
          <h1 className="mt-2 text-title font-bold tracking-tight text-primary">股票监控</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">在一个高密度工作区里扫描价格、指标变化与可关注状态。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {activeTableId !== null ? (
            <>
              <button type="button" onClick={(event) => { addStockTriggerRef.current = event.currentTarget; setAddDialogOpen(true); }} className="inline-flex h-9 items-center justify-center gap-2 rounded-panel bg-brand px-3.5 text-sm font-medium text-white shadow-panel transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/40">
                <span className="text-base leading-none">+</span>
                添加股票
              </button>
              <button
                type="button"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(activeTableId)}
                className="inline-flex h-9 items-center justify-center rounded-panel border border-negative/30 bg-negative/5 px-3 text-sm font-medium text-negative transition hover:bg-negative/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {deleteMutation.isPending ? "删除中…" : "删除当前表格"}
              </button>
            </>
          ) : null}
        </div>
      </section>

      {deleteErrorMessage ? <p className="rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs text-negative" role="alert">{deleteErrorMessage}</p> : null}

      {tablesQuery.isPending ? <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel"><LoadingState label="正在加载监控表…" /></div> : null}
      {tablesQuery.isError ? <div className="rounded-panel border border-line bg-panel shadow-panel"><QueryErrorState error={tablesQuery.error} onRetry={() => tablesQuery.refetch()} /></div> : null}

      {!tablesQuery.isPending && !tablesQuery.isError && tables.length === 0 ? (
        <section className="rounded-panel border border-line bg-panel shadow-panel">
          <EmptyState
            title="还没有监控表"
            description="创建一个监控表后，再添加股票和后端提供的指标列。"
            action={
              <div className="flex flex-col items-center gap-2 sm:flex-row">
                <input
                  value={createName}
                  onChange={(event) => setCreateName(event.target.value)}
                  placeholder="例如：核心观察"
                  aria-label="监控表名称"
                  className="h-9 rounded-panel border border-line bg-card px-3 text-xs text-primary outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
                />
                <button type="button" disabled={!createName.trim() || createMutation.isPending} onClick={() => createMutation.mutate()} className="h-9 rounded-panel bg-brand px-3 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
                  {createMutation.isPending ? "创建中…" : "创建监控表"}
                </button>
              </div>
            }
          />
          {createErrorMessage ? <p className="mx-auto mb-6 max-w-md rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-center text-xs text-negative" role="alert">{createErrorMessage}</p> : null}
        </section>
      ) : null}

      {!tablesQuery.isPending && !tablesQuery.isError && tables.length > 0 ? (
        <>
          <section className="flex min-w-0 items-center gap-2 overflow-x-auto border-b border-line pb-2" aria-label="监控表切换">
            {tables.map((table) => {
              const active = sameId(table.id, activeTableId);
              return (
                <button
                  type="button"
                  key={String(table.id)}
                  onClick={() => selectTable(table.id)}
                  className={`flex shrink-0 items-center gap-2 rounded-panel px-3 py-2 text-xs font-medium transition ${active ? "bg-card text-primary shadow-[inset_0_-2px_0_rgb(var(--color-brand)/1)]" : "text-secondary hover:bg-card/60 hover:text-primary"}`}
                  aria-current={active ? "page" : undefined}
                >
                  {table.name}
                  <span className="rounded-full bg-line/70 px-1.5 py-0.5 text-[10px] text-muted">{table.stock_count}</span>
                </button>
              );
            })}
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_240px]">
            <div className="min-w-0">
              {detailState === "loading" ? <div className="overflow-hidden rounded-panel border border-line bg-panel shadow-panel"><LoadingState /></div> : null}
              {detailState === "error" ? <div className="rounded-panel border border-line bg-panel shadow-panel"><QueryErrorState error={detailsQuery.error} onRetry={() => detailsQuery.refetch()} /></div> : null}
              {detailState === "empty" ? <div className="rounded-panel border border-line bg-panel shadow-panel"><EmptyState title="暂无监控表详情" description="当前监控表暂时没有可展示的数据。" /></div> : null}
              {currentDetail ? (
                <>
                  {detailState === "refreshing" ? <p className="mb-2 rounded-panel border border-line bg-card/50 px-3 py-2 text-xs text-muted" role="status" aria-live="polite">正在刷新监控数据…</p> : null}
                  {detailState === "refresh-error" ? <div className="mb-2 flex items-center justify-between gap-3 rounded-panel border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning" role="alert"><span>刷新失败，当前仍显示上一次数据。</span><button type="button" onClick={() => detailsQuery.refetch()} className="shrink-0 rounded border border-warning/40 px-2 py-1 font-medium hover:bg-warning/10 focus:outline-none focus:ring-2 focus:ring-brand/40">重试</button></div> : null}
                  <WatchTable key={String(currentDetail.id ?? activeTableId)} table={currentDetail} onAddStock={(trigger) => { addStockTriggerRef.current = trigger ?? null; setAddDialogOpen(true); }} />
                </>
              ) : null}
            </div>
            <aside className="h-fit rounded-panel border border-line bg-panel p-card shadow-panel">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold text-primary">工作台状态</p>
                <StateTag tone={detailState === "error" || detailState === "refresh-error" ? "warning" : "positive"}>{detailState === "error" || detailState === "refresh-error" ? "待重试" : detailState === "refreshing" ? "刷新中" : "已连接"}</StateTag>
              </div>
              <dl className="mt-5 divide-y divide-line/80">
                <div className="flex items-center justify-between py-3 first:pt-0">
                  <dt className="text-xs text-muted">当前表格</dt>
                  <dd className="max-w-[120px] truncate text-xs text-secondary">{selectedTable?.name ?? "—"}</dd>
                </div>
                <div className="flex items-center justify-between py-3">
                  <dt className="text-xs text-muted">监控股票</dt>
                  <dd className="text-xs tabular-nums text-secondary">{currentDetail?.stocks.length ?? selectedTable?.stock_count ?? 0}</dd>
                </div>
                <div className="flex items-center justify-between py-3 last:pb-0">
                  <dt className="text-xs text-muted">指标列</dt>
                  <dd className="text-xs tabular-nums text-secondary">{currentDetail?.columns.length ?? "—"}</dd>
                </div>
              </dl>
              <div className="mt-5 rounded-panel border border-warning/20 bg-warning/5 p-3">
                <p className="text-xs font-medium text-warning">数据边界</p>
                <p className="mt-1.5 text-xs leading-5 text-muted">前端只展示后端返回的行情、指标和状态，不在浏览器计算 RSI、MACD 或 BOLL。</p>
              </div>
            </aside>
          </section>
        </>
      ) : null}

      {addDialogOpen && activeTableId !== null ? <AddStockDialog tableId={activeTableId} restoreFocusRef={addStockTriggerRef} onClose={closeAddStockDialog} /> : null}
    </div>
  );
}
