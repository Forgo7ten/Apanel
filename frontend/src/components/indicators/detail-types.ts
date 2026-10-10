import type { IndicatorState, WatchTableColumn, WatchTableStock } from "@/api/types";

export type DetailSelection =
  | { kind: "indicator"; stock: WatchTableStock; column: WatchTableColumn; adjust_type: "qfq" | "none" }
  | { kind: "state"; stock: WatchTableStock; state: IndicatorState };

export type DetailNotificationHandler = (selection: DetailSelection) => void;
