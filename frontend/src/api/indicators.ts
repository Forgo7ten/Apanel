import { apiFetch } from "@/lib/api-client";

import type { IndicatorHistoryPoint, IndicatorSnapshot } from "./types";

export function getIndicators(symbol: string): Promise<IndicatorSnapshot> {
  return apiFetch<IndicatorSnapshot>(`/securities/${encodeURIComponent(symbol)}/indicators`);
}

export function getIndicatorHistory(symbol: string): Promise<IndicatorHistoryPoint[]> {
  return apiFetch<IndicatorHistoryPoint[]>(`/securities/${encodeURIComponent(symbol)}/indicators/history`);
}
