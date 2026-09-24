import { apiFetch } from "@/lib/api-client";

import type {
  AddStockInput,
  CreateColumnInput,
  CreateWatchTableInput,
  Identifier,
  UpdateColumnInput,
  WatchTableColumn,
  WatchTableDetails,
  WatchTableSummary,
} from "./types";

export function getWatchTables(): Promise<WatchTableSummary[]> {
  return apiFetch<WatchTableSummary[]>("/watch-tables");
}

export function createWatchTable(input: CreateWatchTableInput): Promise<WatchTableSummary> {
  return apiFetch<WatchTableSummary>("/watch-tables", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteWatchTable(tableId: Identifier): Promise<void> {
  return apiFetch<void>(`/watch-tables/${encodeURIComponent(tableId)}`, { method: "DELETE" });
}

export function getWatchTable(tableId: Identifier): Promise<WatchTableDetails> {
  return apiFetch<WatchTableDetails>(`/watch-tables/${encodeURIComponent(tableId)}`);
}

export function addStockToWatchTable(tableId: Identifier, input: AddStockInput): Promise<unknown> {
  return apiFetch<unknown>(`/watch-tables/${encodeURIComponent(tableId)}/stocks`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function removeStockFromWatchTable(tableId: Identifier, securityId: Identifier): Promise<void> {
  return apiFetch<void>(
    `/watch-tables/${encodeURIComponent(tableId)}/stocks/${encodeURIComponent(securityId)}`,
    { method: "DELETE" },
  );
}

export function getWatchTableColumns(tableId: Identifier): Promise<WatchTableColumn[]> {
  return apiFetch<WatchTableColumn[]>(`/watch-tables/${encodeURIComponent(tableId)}/columns`);
}

export function createWatchTableColumn(tableId: Identifier, input: CreateColumnInput): Promise<WatchTableColumn> {
  return apiFetch<WatchTableColumn>(`/watch-tables/${encodeURIComponent(tableId)}/columns`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateWatchTableColumn(columnId: Identifier, input: UpdateColumnInput): Promise<WatchTableColumn> {
  return apiFetch<WatchTableColumn>(`/columns/${encodeURIComponent(columnId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteWatchTableColumn(columnId: Identifier): Promise<void> {
  return apiFetch<void>(`/columns/${encodeURIComponent(columnId)}`, { method: "DELETE" });
}
