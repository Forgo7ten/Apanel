import { apiFetch } from "@/lib/api-client";

import type { Identifier, NotificationRecord } from "./types";

export type NotificationHistoryPage = { items: NotificationRecord[]; next_cursor: string | null };
export function getNotificationHistory(cursor: string | null = null, limit = 50): Promise<NotificationHistoryPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiFetch<NotificationHistoryPage>(`/notifications/history?${params}`);
}


export function getNotifications(): Promise<NotificationRecord[]> {
  return apiFetch<NotificationRecord[]>("/notifications");
}

export function retryNotification(notificationId: Identifier): Promise<NotificationRecord> {
  return apiFetch<NotificationRecord>(`/notifications/${encodeURIComponent(notificationId)}/retry`, {
    method: "POST",
  });
}
