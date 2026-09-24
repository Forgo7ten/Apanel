import { apiFetch } from "@/lib/api-client";

import type { AlertRule, CreateAlertInput, Identifier, UpdateAlertInput } from "./types";

export function getAlerts(): Promise<AlertRule[]> {
  return apiFetch<AlertRule[]>("/alerts");
}

export function createAlert(input: CreateAlertInput): Promise<AlertRule> {
  return apiFetch<AlertRule>("/alerts", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateAlert(alertId: Identifier, input: UpdateAlertInput): Promise<AlertRule> {
  return apiFetch<AlertRule>(`/alerts/${encodeURIComponent(alertId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteAlert(alertId: Identifier): Promise<void> {
  return apiFetch<void>(`/alerts/${encodeURIComponent(alertId)}`, { method: "DELETE" });
}
