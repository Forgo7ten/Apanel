import { apiFetch } from "@/lib/api-client";

import type { IndicatorState, StateHistoryPoint } from "./types";

export function getStates(symbol: string): Promise<IndicatorState[]> {
  return apiFetch<IndicatorState[]>(`/securities/${encodeURIComponent(symbol)}/states`);
}

export function getStateHistory(symbol: string): Promise<StateHistoryPoint[]> {
  return apiFetch<StateHistoryPoint[]>(`/securities/${encodeURIComponent(symbol)}/states/history`);
}
