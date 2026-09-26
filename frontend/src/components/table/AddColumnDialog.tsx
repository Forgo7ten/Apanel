"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { createWatchTableColumn } from "@/api/watch";
import type { Identifier, IndicatorViewMode } from "@/api/types";
import { isApiError } from "@/lib/api-errors";
import {
  getDefaultIndicatorParameters,
  toCreateColumnPayload,
  WATCH_INDICATOR_OPTIONS,
} from "@/lib/watch-contract.mjs";

type ParameterField = {
  key: string;
  label: string;
  hint?: string;
};

const VIEW_MODES: Array<{ value: IndicatorViewMode; label: string }> = [
  { value: "COMPOSITE", label: "COMPOSITE 组合" },
  { value: "NUMBER", label: "NUMBER 数值" },
  { value: "DELTA", label: "DELTA 变化" },
  { value: "STATUS", label: "STATUS 状态" },
];

function getParameterFields(indicatorType: string): ParameterField[] {
  switch (indicatorType) {
    case "MA":
    case "PROJECTED_MA":
    case "RSI":
      return [{ key: "period", label: "周期" }];
    case "KDJ":
      return [
        { key: "period", label: "周期" },
        { key: "k_period", label: "K 平滑" },
        { key: "d_period", label: "D 平滑" },
      ];
    case "BOLL":
      return [
        { key: "period", label: "周期" },
        { key: "multiplier", label: "标准差倍数", hint: "后端参数名为 multiplier" },
      ];
    case "MACD":
      return [
        { key: "fast_period", label: "快线周期" },
        { key: "slow_period", label: "慢线周期" },
        { key: "signal_period", label: "信号周期" },
      ];
    default:
      return [];
  }
}

function toFormParameters(indicatorType: string): Record<string, string> {
  return Object.fromEntries(
    Object.entries(getDefaultIndicatorParameters(indicatorType)).map(([key, value]) => [key, String(value)]),
  );
}

function toNumericParameters(parameters: Record<string, string>): Record<string, number> {
  return Object.fromEntries(Object.entries(parameters).map(([key, value]) => [key, Number(value)]));
}

function mutationErrorMessage(error: unknown): string | null {
  if (!error) return null;
  if (isApiError(error)) {
    if (error.status === 404) return "当前监控表不存在或无权限，请刷新后重试。";
    if (error.status === 409) return "该指标列已存在（相同指标和参数不能重复）。";
    return error.message;
  }
  return error instanceof Error ? error.message : "指标列创建失败，请稍后重试。";
}

export function AddColumnDialog({
  tableId,
  onClose,
}: {
  tableId: Identifier;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [indicatorType, setIndicatorType] = useState("MA");
  const [viewMode, setViewMode] = useState<IndicatorViewMode>("COMPOSITE");
  const [parameters, setParameters] = useState(() => toFormParameters("MA"));
  const fields = useMemo(() => getParameterFields(indicatorType), [indicatorType]);
  const createMutation = useMutation({
    mutationFn: () => {
      const payload = toCreateColumnPayload({
        indicatorType,
        viewMode,
        parameters: toNumericParameters(parameters),
      });
      if (Object.values(payload.parameters).some((value) => !Number.isFinite(value as number) || Number(value) <= 0)) {
        throw new Error("指标参数必须是正数。 ");
      }
      return createWatchTableColumn(tableId, payload);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-tables"] }),
        queryClient.invalidateQueries({ queryKey: ["watch-table-columns", tableId] }),
      ]);
      onClose();
    },
  });

  const errorMessage = mutationErrorMessage(createMutation.error);

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-center bg-black/60 px-4 py-6"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && !createMutation.isPending && onClose()}
    >
      <section className="w-full max-w-lg rounded-panel border border-line bg-panel p-5 shadow-panel" role="dialog" aria-modal="true" aria-labelledby="add-column-title">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p id="add-column-title" className="text-base font-semibold text-primary">添加指标列</p>
            <p className="mt-1 text-xs leading-5 text-muted">选择后端已计算的指标与展示模式，参数会按服务端契约持久化。</p>
          </div>
          <button type="button" onClick={onClose} disabled={createMutation.isPending} className="rounded p-1 text-muted hover:bg-card hover:text-primary disabled:opacity-40" aria-label="关闭添加指标列">
            ×
          </button>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block text-xs font-medium text-secondary">
            指标
            <select
              value={indicatorType}
              onChange={(event) => {
                setIndicatorType(event.target.value);
                setParameters(toFormParameters(event.target.value));
              }}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
            >
              {WATCH_INDICATOR_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="block text-xs font-medium text-secondary">
            展示模式
            <select
              value={viewMode}
              onChange={(event) => setViewMode(event.target.value as IndicatorViewMode)}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
            >
              {VIEW_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </select>
          </label>
        </div>

        {fields.length > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {fields.map((field) => (
              <label key={field.key} className="block text-xs font-medium text-secondary">
                {field.label}
                <input
                  type="number"
                  min="1"
                  step={field.key === "multiplier" ? "0.1" : "1"}
                  value={parameters[field.key] ?? ""}
                  onChange={(event) => setParameters((current) => ({ ...current, [field.key]: event.target.value }))}
                  className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm tabular-nums text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
                  aria-label={field.label}
                />
                {field.hint ? <span className="mt-1 block text-[10px] font-normal text-muted">{field.hint}</span> : null}
              </label>
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-panel border border-line/80 bg-card/40 px-3 py-2 text-xs text-muted">该指标不需要额外参数。</p>
        )}

        {errorMessage ? <p className="mt-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{errorMessage}</p> : null}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={createMutation.isPending} className="rounded-panel border border-line bg-card px-3 py-2 text-xs font-medium text-secondary hover:text-primary disabled:opacity-40">取消</button>
          <button type="button" onClick={() => createMutation.mutate()} disabled={createMutation.isPending} className="rounded-panel bg-brand px-3 py-2 text-xs font-medium text-white transition hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-50">
            {createMutation.isPending ? "保存中…" : "添加指标列"}
          </button>
        </div>
      </section>
    </div>
  );
}
