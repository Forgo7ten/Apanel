import { apiFetch } from "@/lib/api-client";
import { buildHistoryPath } from "@/lib/detail-contract.mjs";

import type { HistoryQuery, IndicatorState, StateHistoryResponse } from "./types";

export function getStates(symbol: string): Promise<IndicatorState[]> {
  return apiFetch<IndicatorState[]>(`/securities/${encodeURIComponent(symbol)}/states`);
}

export function getStateHistory(symbol: string, query: HistoryQuery = {}): Promise<StateHistoryResponse> {
  return apiFetch<StateHistoryResponse>(
    buildHistoryPath(`/securities/${encodeURIComponent(symbol)}/states/history`, query),
  );
}
