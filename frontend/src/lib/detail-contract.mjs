import { orderIndicatorFields } from "./indicator-contract.mjs";

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => key !== "field")
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

export function buildHistoryPath(path, query = {}) {
  const params = new URLSearchParams();
  if (query.start) params.set("start", query.start);
  if (query.end) params.set("end", query.end);
  if (query.adjust) params.set("adjust", query.adjust);
  if (query.parameter_key) params.set("parameter_key", query.parameter_key);
  const suffix = params.toString();
  return `${path}${suffix ? `?${suffix}` : ""}`;
}

export function historyWindow(end = new Date()) {
  const endDate = new Date(end);
  const startDate = new Date(endDate);
  startDate.setUTCDate(startDate.getUTCDate() - 89);
  return {
    start: startDate.toISOString().slice(0, 10),
    end: endDate.toISOString().slice(0, 10),
  };
}

export function matchesHistoryParameters(point, column, parameterKey) {
  if (!isRecord(point) || !isRecord(column)) return false;
  if (String(point.indicator_type ?? "").toUpperCase() !== String(column.indicator_type ?? "").toUpperCase()) return false;
  const expectedKey = parameterKey ?? column.parameter_key;
  if (point.parameter_key && expectedKey) return point.parameter_key === expectedKey;
  const requested = stableValue(column.parameters ?? {});
  const stored = stableValue(point.parameters ?? {});
  // Legacy v1 rows have no selector.  Keep this fallback exact so a missing
  // key never turns a same-type column into an arbitrary parameter match.
  return JSON.stringify(requested) === JSON.stringify(stored);
}

export function selectHistoryPoints(items, column, parameterKey) {
  return (Array.isArray(items) ? items : []).filter((point) => matchesHistoryParameters(point, column, parameterKey));
}

export function historyValue(point, column) {
  if (!isRecord(point) || !isRecord(point.values)) return null;
  const requestedField = isRecord(column?.parameters) ? column.parameters.field : undefined;
  const candidates = requestedField
    ? [requestedField]
    : ["value", ...Object.keys(point.values).sort()];
  for (const key of candidates) {
    const value = point.values[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

export function toMiniChartPoints(items, column, parameterKey) {
  return selectHistoryPoints(items, column, parameterKey)
    .map((point) => ({ date: point.trade_date, value: historyValue(point, column) }))
    .filter((point) => point.value !== null);
}

export function miniChartGeometry(points, width = 280, height = 72) {
  const finite = (Array.isArray(points) ? points : []).filter((point) => Number.isFinite(point?.value));
  if (finite.length === 0) return [];
  if (finite.length === 1) return [{ x: width / 2, y: height / 2, ...finite[0] }];
  const values = finite.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return finite.map((point, index) => ({
    ...point,
    x: (index / (finite.length - 1)) * width,
    y: height - ((point.value - min) / range) * height,
  }));
}

export function detailViewState({ loading = false, error = false, itemCount = 0 }) {
  if (loading) return "loading";
  if (error) return "error";
  return itemCount > 0 ? "ready" : "empty";
}

export function miniChartPointLabel(point) {
  return `${point.date}：${point.value}`;
}

export function selectStateHistory(items, stateCode) {
  return (Array.isArray(items) ? items : []).filter((item) => (
    item?.state_code === stateCode || (!item?.state_code && item?.state_id === stateCode)
  ));
}

export function selectRelatedStates(items, indicatorType) {
  const expected = String(indicatorType ?? "").toUpperCase();
  if (!expected) return [];
  return (Array.isArray(items) ? items : []).filter((item) => (
    String(item?.indicator_type ?? "").toUpperCase() === expected
  ));
}

export function isValidDetailSelection(selection) {
  if (!isRecord(selection) || !isRecord(selection.stock)) return false;
  if (selection.kind === "indicator") return isRecord(selection.column) && !Object.hasOwn(selection, "state");
  if (selection.kind === "state") return isRecord(selection.state) && !Object.hasOwn(selection, "column");
  return false;
}

export function focusLoopIndex(current, count, shift) {
  if (!Number.isInteger(count) || count <= 0) return -1;
  const step = shift ? -1 : 1;
  return (current + step + count) % count;
}

export function isDetailActivationKey(key) {
  return key === "Enter" || key === " " || key === "Spacebar";
}

export function isDetailCloseKey(key) {
  return key === "Escape";
}

export function orderHistoryFields(fields) {
  return orderIndicatorFields(Object.entries(isRecord(fields) ? fields : {}));
}
