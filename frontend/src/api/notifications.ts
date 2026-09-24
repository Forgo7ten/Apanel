import { apiFetch } from "@/lib/api-client";

import type { NotificationRecord } from "./types";

export function getNotifications(): Promise<NotificationRecord[]> {
  return apiFetch<NotificationRecord[]>("/notifications");
}
