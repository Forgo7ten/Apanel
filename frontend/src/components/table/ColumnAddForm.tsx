"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { createWatchTableColumn } from "@/api/watch";
import type { Identifier, IndicatorViewMode } from "@/api/types";
import { isApiError } from "@/lib/api-errors";
import { getIndicatorDisplayTitle } from "@/lib/indicator-metadata.mjs";
import { ALERT_STATE_OPTIONS } from "@/lib/notification-contract.mjs";
import {
  getDefaultIndicatorParameters,
  getIndicatorFieldOptions,
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

function compatibleStateOptions(indicatorType: string) {
  const prefix = `${indicatorType.toUpperCase()}_`;
  return ALERT_STATE_OPTIONS.filter((option) => option.id.startsWith(prefix));
}

function toStatusFormParameters(indicatorType: string): Record<string, string> {
  if (indicatorType === "MA") return { short_period: "5", long_period: "10" };
  return toFormParameters(indicatorType);
}

function getStatusParameterFields(indicatorType: string): ParameterField[] {
  if (indicatorType === "MA") {
    return [
      { key: "short_period", label: "短周期" },
      { key: "long_period", label: "长周期" },
    ];
  }
  return getParameterFields(indicatorType);
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

export function ColumnAddForm({
  tableId,
  onCreated,
}: {
  tableId: Identifier;
  onCreated?: () => void;
}) {
  const queryClient = useQueryClient();
  const [indicatorType, setIndicatorType] = useState("MA");
  const [viewMode, setViewMode] = useState<IndicatorViewMode>("COMPOSITE");
  const [parameters, setParameters] = useState(() => toFormParameters("MA"));
  const [field, setField] = useState("");
  const [stateCode, setStateCode] = useState("");
  const dividendYield = indicatorType === "DIVIDEND_YIELD";
  const effectiveViewMode: IndicatorViewMode = dividendYield ? "NUMBER" : viewMode;
  const stateOptions = useMemo(() => compatibleStateOptions(indicatorType), [indicatorType]);
  const fields = useMemo(
    () => effectiveViewMode === "STATUS" ? getStatusParameterFields(indicatorType) : getParameterFields(indicatorType),
    [effectiveViewMode, indicatorType],
  );
  const fieldOptions = useMemo(() => getIndicatorFieldOptions(indicatorType), [indicatorType]);
  const requiresField = (effectiveViewMode === "NUMBER" || effectiveViewMode === "DELTA") && fieldOptions.length > 0;
  const fieldError = requiresField && field.length === 0 ? "NUMBER/DELTA 模式需要选择明确字段。" : null;
  const stateError = effectiveViewMode === "STATUS" && stateCode.length === 0 ? "STATUS 模式需要选择明确状态。" : null;
  const numericParameters = toNumericParameters(parameters);
  const previewTitle = getIndicatorDisplayTitle(indicatorType, numericParameters, indicatorType);

  const createMutation = useMutation({
    mutationFn: () => {
      if (fieldError) throw new Error(fieldError);
      if (stateError) throw new Error(stateError);
      if (Object.values(numericParameters).some((value) => !Number.isFinite(value) || value <= 0)) {
        throw new Error("指标参数必须是正数。");
      }
      const payload = toCreateColumnPayload({
        indicatorType,
        viewMode: effectiveViewMode,
        stateCode,
        parameters: effectiveViewMode === "STATUS"
          ? numericParameters
          : { ...numericParameters, ...(field ? { field } : {}) },
      });
      return createWatchTableColumn(tableId, payload);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["watch-table", tableId] }),
        queryClient.invalidateQueries({ queryKey: ["watch-tables"] }),
        queryClient.invalidateQueries({ queryKey: ["watch-table-columns", tableId] }),
      ]);
      onCreated?.();
    },
  });

  const errorMessage = mutationErrorMessage(createMutation.error);

  function selectIndicator(nextIndicator: string) {
    setIndicatorType(nextIndicator);
    if (nextIndicator === "DIVIDEND_YIELD") {
      setViewMode("NUMBER");
      setParameters({});
      setStateCode("");
      setField("");
      return;
    }
    if (effectiveViewMode === "STATUS") {
      const options = compatibleStateOptions(nextIndicator);
      setParameters(toStatusFormParameters(nextIndicator));
      setStateCode(options[0]?.id ?? "");
    } else {
      setParameters(toFormParameters(nextIndicator));
      setStateCode("");
    }
    setField("");
  }

  function selectViewMode(nextMode: IndicatorViewMode) {
    if (dividendYield) return;
    setViewMode(nextMode);
    if (nextMode === "STATUS") {
      setField("");
      setParameters(toStatusFormParameters(indicatorType));
      setStateCode(compatibleStateOptions(indicatorType)[0]?.id ?? "");
    } else {
      setParameters(toFormParameters(indicatorType));
      setStateCode("");
      if (nextMode === "COMPOSITE") setField("");
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-panel border border-line/80 bg-card/30 p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-xs font-medium text-secondary">
            指标
            <select
              value={indicatorType}
              onChange={(event) => selectIndicator(event.target.value)}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
            >
              {WATCH_INDICATOR_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="block text-xs font-medium text-secondary">
            展示模式
            <select
              value={effectiveViewMode}
              disabled={dividendYield}
              onChange={(event) => selectViewMode(event.target.value as IndicatorViewMode)}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {dividendYield
                ? <option value="NUMBER">NUMBER 数值（固定）</option>
                : VIEW_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </select>
          </label>
        </div>

        {indicatorType === "PROJECTED_MA" ? (
          <p className="mt-3 rounded-panel border border-brand/20 bg-brand/5 px-3 py-2 text-xs text-secondary">
            表格列名预览：<span className="font-medium text-primary">{previewTitle}</span>
          </p>
        ) : null}
        {dividendYield ? (
          <p className="mt-3 rounded-panel border border-line/80 bg-card px-3 py-2 text-xs leading-5 text-muted">
            股息率固定使用数值模式，表格中按百分数显示并保留 3 位小数。
          </p>
        ) : null}

        {effectiveViewMode === "STATUS" ? (
          <label className="mt-4 block text-xs font-medium text-secondary">
            状态
            <select
              value={stateCode}
              onChange={(event) => setStateCode(event.target.value)}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
              aria-label="状态目标"
            >
              {stateOptions.length === 0 ? <option value="">该指标暂无可识别状态</option> : null}
              {stateOptions.map((option) => <option key={option.id} value={option.id}>{option.title} · {option.id}</option>)}
            </select>
          </label>
        ) : null}

        {fields.length > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {fields.map((parameterField) => (
              <label key={parameterField.key} className="block text-xs font-medium text-secondary">
                {parameterField.label}
                <input
                  type="number"
                  min="1"
                  step={parameterField.key === "multiplier" ? "0.1" : "1"}
                  value={parameters[parameterField.key] ?? ""}
                  onChange={(event) => setParameters((current) => ({ ...current, [parameterField.key]: event.target.value }))}
                  className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm tabular-nums text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
                  aria-label={parameterField.label}
                />
                {parameterField.hint ? <span className="mt-1 block text-[10px] font-normal text-muted">{parameterField.hint}</span> : null}
              </label>
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-panel border border-line/80 bg-card/40 px-3 py-2 text-xs text-muted">该指标不需要额外参数。</p>
        )}

        {fieldOptions.length > 0 && effectiveViewMode !== "COMPOSITE" ? (
          <label className="mt-4 block text-xs font-medium text-secondary">
            字段
            <select
              value={field}
              onChange={(event) => setField(event.target.value)}
              className="mt-2 h-10 w-full rounded-panel border border-line bg-card px-3 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
              aria-label="指标字段"
            >
              <option value="">请选择字段</option>
              {fieldOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
        ) : null}

        {fieldError ? <p className="mt-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{fieldError}</p> : null}
        {stateError ? <p className="mt-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{stateError}</p> : null}
        {errorMessage ? <p className="mt-4 rounded-panel border border-negative/30 bg-negative/10 px-3 py-2 text-xs leading-5 text-negative" role="alert">{errorMessage}</p> : null}

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending || Boolean(fieldError) || Boolean(stateError)}
            className="rounded-panel bg-brand px-4 py-2 text-xs font-medium text-white transition hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {createMutation.isPending ? "保存中…" : `添加「${previewTitle}」`}
          </button>
        </div>
      </div>
    </div>
  );
}
