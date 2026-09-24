import type { IndicatorState, WatchTableColumn, WatchTableStock } from "@/api/types";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function getDisplayNumber(value: unknown): number | string | null {
  if (typeof value === "number" || typeof value === "string") {
    return value;
  }

  if (isRecord(value)) {
    return getDisplayNumber(value.value ?? value.price ?? value.current);
  }

  return null;
}

export function formatMetricValue(value: unknown): string {
  const displayValue = getDisplayNumber(value);

  if (displayValue === null || displayValue === "") {
    return "—";
  }

  if (typeof displayValue === "number") {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(displayValue);
  }

  return displayValue;
}

export function getDelta(value: unknown): number | string | null {
  if (!isRecord(value)) {
    return null;
  }

  const delta = value.delta ?? value.change;
  return typeof delta === "number" || typeof delta === "string" ? delta : null;
}

export function getDirection(value: unknown): "UP" | "DOWN" | "FLAT" | null {
  if (!isRecord(value)) {
    return null;
  }

  const direction = value.direction;
  if (typeof direction === "string") {
    const normalized = direction.toUpperCase();
    if (normalized === "UP" || normalized === "DOWN" || normalized === "FLAT") {
      return normalized;
    }
  }

  const delta = getDelta(value);
  if (typeof delta === "number") {
    return delta > 0 ? "UP" : delta < 0 ? "DOWN" : "FLAT";
  }

  return null;
}

export function getIndicatorValue(stock: WatchTableStock, column: WatchTableColumn): unknown {
  const keys = [column.key, column.indicator_type, column.type, column.id === undefined ? undefined : String(column.id)].filter(
    (key): key is string => typeof key === "string" && key.length > 0,
  );
  const sources = [stock.indicators, stock.indicator_values, stock.values];

  for (const source of sources) {
    if (!source) continue;

    for (const key of keys) {
      if (source[key] !== undefined) {
        return source[key];
      }
    }
  }

  return undefined;
}

export function getColumnTitle(column: WatchTableColumn): string {
  return column.title ?? column.label ?? column.indicator_type ?? column.type;
}

export function getStateToneLevel(state: IndicatorState): string | undefined {
  return state.level ?? state.severity;
}
