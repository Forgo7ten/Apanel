declare module "@/lib/watch-alert-contract.mjs" {
  import type { IndicatorParameters, WatchTableStock, WatchTableColumn, IndicatorState } from "@/api/types";
  type Adjustment = "qfq" | "none";
  export function buildDetailAlertDraft(selection:
    | { kind: "indicator"; stock: WatchTableStock; column: WatchTableColumn; adjust_type: Adjustment }
    | { kind: "state"; stock: WatchTableStock; state: IndicatorState }
  ): {
    security: { id: string | number; security_id: string | number; symbol: string; name: string; market: string };
    condition_type: "STATE" | "VALUE"; state_id?: string; indicator?: string;
    parameters: IndicatorParameters; field?: string; adjust_type: Adjustment;
  };
  export function numericParameters(raw: IndicatorParameters): IndicatorParameters;
  export function maPeriods(parameters?: IndicatorParameters): number[];
  export function availableMaPeriods(parameters?: IndicatorParameters): number[];
  export function initialValueParameters(indicator: string, supplied?: IndicatorParameters | null): IndicatorParameters;
}
