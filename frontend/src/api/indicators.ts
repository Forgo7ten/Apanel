import { apiFetch } from "@/lib/api-client";
import { buildHistoryPath } from "@/lib/detail-contract.mjs";

import type { HistoryQuery, IndicatorHistoryResponse, IndicatorSnapshot } from "./types";

export function getIndicators(symbol: string): Promise<IndicatorSnapshot> {
  return apiFetch<IndicatorSnapshot>(`/securities/${encodeURIComponent(symbol)}/indicators`);
}

export function getIndicatorHistory(symbol: string, query: HistoryQuery = {}): Promise<IndicatorHistoryResponse> {
  return apiFetch<IndicatorHistoryResponse>(
    buildHistoryPath(`/securities/${encodeURIComponent(symbol)}/indicators/history`, query),
  );
}
