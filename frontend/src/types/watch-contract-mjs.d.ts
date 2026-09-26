declare module "@/lib/watch-contract.mjs" {
  export function getSecurityIdentifier(security: unknown): number | string | null;
  export function toAddStockPayload(security: unknown): { security_id: number | string } | null;
  export const WATCH_INDICATOR_OPTIONS: ReadonlyArray<{ value: string; label: string }>;
  export function getIndicatorFieldOptions(indicatorType: unknown): ReadonlyArray<{ value: string; label: string }>;
  export function getDefaultIndicatorParameters(indicatorType: unknown): Record<string, number>;
  export function toCreateColumnPayload(input: {
    indicatorType: unknown;
    viewMode?: string;
    parameters?: Record<string, number | string | boolean | null | number[] | string[]>;
  }): {
    column_type: "INDICATOR";
    indicator_type: string;
    parameters: Record<string, number | string | boolean | null | number[] | string[]>;
    view_mode: "NUMBER" | "DELTA" | "STATUS" | "COMPOSITE";
  };
  export function toColumnOrderPayload(columnOrder: unknown): { column_ids: number[] };
}
