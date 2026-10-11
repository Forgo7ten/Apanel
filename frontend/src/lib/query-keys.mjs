/**
 * The single vocabulary for React Query reads and mutations.
 * Prefix keys are only for a known resource scope. Never use a detail prefix
 * with setQueryData: it would silently write an unused cache entry.
 */
export const queryKeys = Object.freeze({
  watchTables: () => ["watch-tables"],
  watchTablePrefix: (tableId) => ["watch-table", tableId],
  watchTable: (tableId, adjustment) => ["watch-table", tableId, adjustment],
  securitySearch: (query) => ["securities", "search", query.trim()],
  settings: () => ["settings"],
  alerts: () => ["alerts"],
  notifications: () => ["notifications"],
  notificationHistory: () => ["notifications", "history"],
  indicatorHistory: (symbol, start, end, adjustment, parameterKey) => [
    "indicator-history", symbol, start, end, adjustment, parameterKey ?? null,
  ],
  stateHistory: (symbol, start, end, adjustment, filter) => [
    "state-history", symbol, start, end, adjustment, filter,
  ],
  backendHealth: () => ["backend-health"],
});
