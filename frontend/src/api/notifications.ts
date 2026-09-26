import { apiFetch } from "@/lib/api-client";

import type { Identifier, NotificationRecord } from "./types";

export function getNotifications(): Promise<NotificationRecord[]> {
  return apiFetch<NotificationRecord[]>("/notifications");
}

export function retryNotification(notificationId: Identifier): Promise<NotificationRecord> {
  return apiFetch<NotificationRecord>(`/notifications/${encodeURIComponent(notificationId)}/retry`, {
    method: "POST",
  });
}
