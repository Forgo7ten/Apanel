declare module "@/lib/detail-contract.mjs" {
  export function buildHistoryPath(path: string, query?: Record<string, string | undefined>): string;
  export function historyWindow(end?: Date): { start: string; end: string };
  export function matchesHistoryParameters(point: unknown, column: unknown, parameterKey?: string): boolean;
  export function selectHistoryPoints<T = Record<string, unknown>>(items: unknown, column: unknown, parameterKey?: string): T[];
  export function historyValue(point: unknown, column: unknown): number | null;
  export function toMiniChartPoints(items: unknown, column: unknown, parameterKey?: string): Array<{ date: string; value: number }>;
  export function miniChartGeometry(
    points: Array<{ date: string; value: number }>,
    width?: number,
    height?: number,
  ): Array<{ date: string; value: number; x: number; y: number }>;
  export function detailViewState(options: { loading?: boolean; error?: boolean; itemCount?: number }): "loading" | "error" | "empty" | "ready";
  export function miniChartPointLabel(point: { date: string; value: number }): string;
  export function selectStateHistory<T = Record<string, unknown>>(items: unknown, stateCode: string): T[];
  export function selectRelatedStates<T = Record<string, unknown>>(items: unknown, indicatorType: string): T[];
  export function isValidDetailSelection(selection: unknown): boolean;
  export function focusLoopIndex(current: number, count: number, shift: boolean): number;
  export function isDetailActivationKey(key: string): boolean;
  export function isDetailCloseKey(key: string): boolean;
  export function orderHistoryFields(fields: unknown): Array<[string, unknown]>;
}
