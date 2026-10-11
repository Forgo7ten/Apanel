declare module "@/lib/state-column-contract.mjs" {
  import type { IndicatorState, WatchTableColumn } from "@/api/types";
  export function stateMatchesIndicatorColumn(state: IndicatorState, column: WatchTableColumn): boolean;
  export function selectColumnStateHistory<T extends IndicatorState>(items: T[] | undefined | null, column: WatchTableColumn): T[];
}
