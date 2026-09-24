import { apiFetch } from "@/lib/api-client";

import type { UpdateSettingsInput, UserSettings } from "./types";

export function getSettings(): Promise<UserSettings | null> {
  return apiFetch<UserSettings | null>("/settings");
}

export function updateSettings(input: UpdateSettingsInput): Promise<UserSettings | null> {
  return apiFetch<UserSettings | null>("/settings", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}
