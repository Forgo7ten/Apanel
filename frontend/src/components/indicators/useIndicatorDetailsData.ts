"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { getIndicatorHistory } from "@/api/indicators";
import { getStateHistory } from "@/api/states";
import type { IndicatorHistoryPoint } from "@/api/types";
import {
  detailViewState,
  historyValue,
  historyWindow,
  selectHistoryPoints,
  selectStateHistory,
  toMiniChartPoints,
} from "@/lib/detail-contract.mjs";

import type { DetailSelection } from "./detail-types";
import { getIndicatorValue, isRecord } from "./indicator-utils";
import type { MiniChartPoint } from "./MiniChart";

function persistedParameterKey(selection: DetailSelection | null): string | undefined {
  if (!selection || selection.kind !== "indicator") return undefined;
  const value = getIndicatorValue(selection.stock, selection.column);
  if (!isRecord(value) || typeof value.parameter_key !== "string") return undefined;
  return value.parameter_key;
}

export function useIndicatorDetailsData(selection: DetailSelection | null) {
  const range = useMemo(() => historyWindow(), []);
  const open = selection !== null;
  const symbol = selection?.stock.symbol ?? "";
  const parameterKey = persistedParameterKey(selection);
  const historyColumn = useMemo(() => {
    if (!selection || selection.kind !== "indicator") return null;
    return parameterKey ? { ...selection.column, parameter_key: parameterKey } : selection.column;
  }, [parameterKey, selection]);
  const indicatorQuery = useQuery({
    queryKey: ["indicator-history", symbol, range.start, range.end, parameterKey ?? null],
    queryFn: () => getIndicatorHistory(symbol, {
      ...range,
      adjust: "qfq",
      ...(parameterKey ? { parameter_key: parameterKey } : {}),
    }),
    enabled: open && Boolean(historyColumn),
  });
  const stateQuery = useQuery({
    queryKey: ["state-history", symbol, range.start, range.end],
    queryFn: () => getStateHistory(symbol, { ...range, adjust: "qfq" }),
    enabled: open,
  });

  const indicatorItems = useMemo<IndicatorHistoryPoint[]>(() => (
    historyColumn
      ? selectHistoryPoints(indicatorQuery.data?.items, historyColumn, parameterKey)
      : []
  ), [historyColumn, indicatorQuery.data?.items, parameterKey]);
  const chartPoints = useMemo<MiniChartPoint[]>(() => (
    historyColumn
      ? toMiniChartPoints(indicatorQuery.data?.items, historyColumn, parameterKey) as MiniChartPoint[]
      : []
  ), [historyColumn, indicatorQuery.data?.items, parameterKey]);
  const stateItems = useMemo(() => {
    const items = stateQuery.data?.items ?? [];
    if (!selection || selection.kind !== "state") return items;
    return selectStateHistory(items, selection.state.state_code ?? selection.state.state_id);
  }, [selection, stateQuery.data?.items]);
  const indicatorState = detailViewState({
    loading: Boolean(historyColumn && indicatorQuery.isPending),
    error: Boolean(historyColumn && indicatorQuery.isError),
    itemCount: indicatorItems.length,
  });

  return {
    range,
    indicatorQuery,
    stateQuery,
    indicatorItems,
    chartPoints,
    stateItems,
    indicatorState,
    latestValue: historyValue(indicatorItems[indicatorItems.length - 1], historyColumn),
    currentValue: selection && "column" in selection ? getIndicatorValue(selection.stock, selection.column) : null,
    parameterKey,
  };
}
