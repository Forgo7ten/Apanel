import type { IndicatorState, WatchTableColumn, WatchTableStock } from "@/api/types";
import {
  getColumnFieldValue,
  getColumnValue,
} from "@/lib/indicator-contract.mjs";

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

export function getPreviousValue(value: unknown): number | string | null {
  if (!isRecord(value)) return null;
  const previous = getColumnFieldValue(value, "previous_value");
  return typeof previous === "number" || typeof previous === "string" ? previous : null;
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
  return getColumnValue(stock, column);
}

export function getFieldValue(value: unknown, key: "value" | "previous_value" | "delta" | "direction"): unknown {
  return getColumnFieldValue(value, key);
}

export function getColumnTitle(column: WatchTableColumn): string {
  return column.title ?? column.label ?? column.indicator_type ?? column.type;
}

export function getStateToneLevel(state: IndicatorState): string | undefined {
  return state.level ?? state.severity;
}
