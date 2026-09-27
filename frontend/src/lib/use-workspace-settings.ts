"use client";
import { useQuery } from "@tanstack/react-query";
import { getSettings } from "@/api/settings";
import { normalizeSettings } from "@/lib/settings-contract.mjs";

export function useWorkspaceSettings() {
  const query = useQuery({ queryKey: ["settings"], queryFn: getSettings, staleTime: 60_000 });
  return { query, settings: normalizeSettings(query.data ?? {}) };
}
