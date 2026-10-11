declare module "@/lib/query-keys.mjs" {
  import type { Identifier } from "@/api/types";
  type Key = readonly unknown[];
  export const queryKeys: Readonly<{
    watchTables(): Key;
    watchTablePrefix(tableId: Identifier): Key;
    watchTable(tableId: Identifier | null, adjustment: "qfq" | "none"): Key;
    securitySearch(query: string): Key;
    settings(): Key;
    alerts(): Key;
    notifications(): Key;
    notificationHistory(): Key;
    indicatorHistory(symbol: string, start: string, end: string, adjustment: string, parameterKey?: string | null): Key;
    stateHistory(symbol: string, start: string, end: string, adjustment: string, filter: Record<string, unknown>): Key;
    backendHealth(): Key;
  }>;
}
