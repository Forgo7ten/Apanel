declare module "@/lib/column-mode-contract.mjs" {
  import type { IndicatorParameters, UpdateColumnInput, WatchTableColumn } from "@/api/types";
  export function columnModeParameters(source: WatchTableColumn, targetMode: string): IndicatorParameters;
  export function columnModeFields(indicatorType: string, parameters: IndicatorParameters): { value: string; label: string }[];
  export function buildColumnModeUpdate(input: {
    indicatorType: string;
    viewMode: string;
    parameters: IndicatorParameters;
    field?: string;
    stateCode?: string;
  }): UpdateColumnInput;
}
