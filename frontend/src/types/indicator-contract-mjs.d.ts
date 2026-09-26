declare module "@/lib/indicator-contract.mjs" {
  export function getColumnValue(stock: unknown, column: unknown): unknown;
  export function getColumnFields(value: unknown): Array<[string, unknown]>;
  export function orderIndicatorFields(entries: Array<[string, unknown]>): Array<[string, unknown]>;
  export function getColumnFieldValue(field: unknown, key: "value" | "previous_value" | "delta" | "direction"): unknown;
}
