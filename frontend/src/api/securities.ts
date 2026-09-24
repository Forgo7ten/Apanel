import { apiFetch } from "@/lib/api-client";

import type { DailyBar, QuoteSnapshot, Security } from "./types";

export function searchSecurities(query: string): Promise<Security[]> {
  const params = new URLSearchParams({ q: query });
  return apiFetch<Security[]>(`/securities/search?${params.toString()}`);
}

export function getSecurity(symbol: string): Promise<Security> {
  return apiFetch<Security>(`/securities/${encodeURIComponent(symbol)}`);
}

export function getQuote(symbol: string): Promise<QuoteSnapshot> {
  return apiFetch<QuoteSnapshot>(`/securities/${encodeURIComponent(symbol)}/quote`);
}

export function getDailyBars(symbol: string, startDate?: string, endDate?: string, adjustType?: string): Promise<DailyBar[]> {
  const params = new URLSearchParams();

  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (adjustType) params.set("adjust_type", adjustType);

  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<DailyBar[]>(`/securities/${encodeURIComponent(symbol)}/daily-bars${suffix}`);
}
