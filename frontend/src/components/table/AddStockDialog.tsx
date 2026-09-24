"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { searchSecurities } from "@/api/securities";
import type { Identifier, Security } from "@/api/types";
import { addStockToWatchTable } from "@/api/watch";
import { isApiError } from "@/lib/api-errors";
import { getSecurityIdentifier, toAddStockPayload } from "@/lib/watch-contract.mjs";

function securityId(security: Security): Identifier | null {
  return getSecurityIdentifier(security);
}

export function AddStockDialog({
  tableId,
  onClose,
}: {
  tableId: Identifier;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Security | null>(null);
  const trimmedQuery = query.trim();
  const searchQuery = useQuery({
    queryKey: ["securities", "search", trimmedQuery],
    queryFn: () => searchSecurities(trimmedQuery),
    enabled: trimmedQuery.length >= 2,
    staleTime: 60_000,
  });
  const addMutation = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请选择一只股票。");
      const payload = toAddStockPayload(selected);
      if (payload === null) throw new Error("搜索结果缺少 security_id，暂时无法添加。");
      return addStockToWatchTable(tableId, payload);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-tables"] }),
      ]);
      onClose();
    },
  });

  const error = addMutation.error ?? searchQuery.error;
  const errorMessage = isApiError(error) && error.status === 404
    ? "后端尚未提供添加股票接口，请完成服务端路由后重试。"
    : error instanceof Error
      ? error.message
      : null;

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/60 px-4 py-6" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="w-full max-w-lg rounded-panel border border-line bg-panel p-5 shadow-panel" role="dialog" aria-modal="true" aria-labelledby="add-stock-title">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-base font-semibold text-primary" id="add-stock-title">添加股票</p>
            <p className="mt-1 text-xs text-muted">搜索证券后加入当前监控表，指标由后端返回。</p>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-muted hover:bg-card hover:text-primary" aria-label="关闭添加股票">
            ×
          </button>
        </div>

        <label className="mt-5 block text-xs font-medium text-secondary" htmlFor="security-search">证券代码或名称</label>
        <input
          id="security-search"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setSelected(null);
          }}
          autoFocus
          placeholder="例如 600519 或 贵州茅台"
          className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
        />

        <div className="mt-3 min-h-28 rounded-panel border border-line/80 bg-card/40 p-2">
          {trimmedQuery.length < 2 ? (
            <p className="grid min-h-24 place-items-center text-xs text-muted">输入至少 2 个字符开始搜索。</p>
          ) : searchQuery.isPending ? (
            <p className="grid min-h-24 place-items-center text-xs text-muted" aria-live="polite">正在搜索证券…</p>
          ) : searchQuery.data?.length ? (
            <div className="space-y-1" role="listbox" aria-label="证券搜索结果">
              {searchQuery.data.map((security) => {
                const id = securityId(security);
                const active = selected?.symbol === security.symbol;
                return (
                  <button
                    type="button"
                    key={`${security.symbol}-${String(id ?? "missing")}`}
                    onClick={() => setSelected(security)}
                    className={`flex w-full items-center justify-between rounded px-3 py-2 text-left transition ${active ? "bg-brand/15 ring-1 ring-brand/60" : "hover:bg-panel"}`}
                    role="option"
                    aria-selected={active}
                  >
                    <span>
                      <span className="block text-sm font-medium text-primary">{security.name}</span>
                      <span className="mt-0.5 block font-mono text-[11px] text-muted">{security.symbol} · {security.market}</span>
                    </span>
                    {id === null ? <span className="text-[10px] text-warning">缺少 ID</span> : null}
                  </button>
                );
              })}
            </div>
          ) : searchQuery.isError ? (
            <p className="grid min-h-24 place-items-center px-3 text-center text-xs text-warning">{isApiError(searchQuery.error) && searchQuery.error.status === 404 ? "后端尚未提供证券搜索接口。" : "证券搜索失败，请稍后重试。"}</p>
          ) : (
            <p className="grid min-h-24 place-items-center text-xs text-muted">没有匹配的证券。</p>
          )}
        </div>

        {errorMessage ? <p className="mt-3 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{errorMessage}</p> : null}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-panel border border-line bg-card px-3 py-2 text-xs font-medium text-secondary hover:text-primary">取消</button>
          <button
            type="button"
            disabled={!selected || securityId(selected) === null || addMutation.isPending}
            onClick={() => addMutation.mutate()}
            className="rounded-panel bg-brand px-3 py-2 text-xs font-medium text-white transition hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {addMutation.isPending ? "添加中…" : "添加到监控表"}
          </button>
        </div>
      </section>
    </div>
  );
}
