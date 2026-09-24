"use client";

import { create } from "zustand";

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

export const useWatchStore = create<WatchStore>((set) => ({
  selectedTableId: null,
  columnVisibility: {},
  columnOrder: [],
  sorting: [],
  setSelectedTableId: (selectedTableId) => set({ selectedTableId }),
  setColumnVisibility: (columnVisibility) => set({ columnVisibility }),
  setColumnOrder: (columnOrder) => set({ columnOrder }),
  setSorting: (sorting) => set({ sorting }),
  resetTableView: () => set({ columnVisibility: {}, columnOrder: [], sorting: [] }),
}));
