"use client";

import { create } from "zustand";

import { sessionScope } from "@/lib/session-scope.mjs";

export type WatchSort = {
  id: string;
  desc: boolean;
};

type WatchStore = {
  selectedTableId: string | number | null;
  columnVisibility: Record<string, boolean>;
  columnOrder: string[];
  sorting: WatchSort[];
  setSelectedTableId: (tableId: string | number | null) => void;
  setColumnVisibility: (columnVisibility: Record<string, boolean>) => void;
  setColumnOrder: (columnOrder: string[]) => void;
  setSorting: (sorting: WatchSort[]) => void;
  resetTableView: () => void;
};

function accountState(set: (patch: Partial<WatchStore>) => void): WatchStore {
  const version = sessionScope.version();
  const update = (patch: Partial<WatchStore>) => {
    // A mutation callback may have awaited before the account changed. Its
    // captured actions must not touch the new account, even after a reset.
    if (sessionScope.version() === version) set(patch);
  };
  return {
    selectedTableId: null,
    columnVisibility: {},
    columnOrder: [],
    sorting: [],
    setSelectedTableId: (selectedTableId) => update({ selectedTableId }),
    setColumnVisibility: (columnVisibility) => update({ columnVisibility }),
    setColumnOrder: (columnOrder) => update({ columnOrder }),
    setSorting: (sorting) => update({ sorting }),
    resetTableView: () => update({ columnVisibility: {}, columnOrder: [], sorting: [] }),
  };
}

export const useWatchStore = create<WatchStore>((set) => accountState(set));

// Renew both values and actions synchronously. A -> B -> A is a new lifetime,
// while an access-token refresh for the same account preserves current actions.
sessionScope.subscribe(() => {
  useWatchStore.setState(accountState(useWatchStore.setState), true);
});
