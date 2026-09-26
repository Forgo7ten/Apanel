/**
 * Normalize the two identifier spellings used by securities responses.
 * A missing identifier is intentionally represented as null so the UI cannot
 * invent a security_id from a display symbol.
 *
 * @param {unknown} security
 * @returns {number|string|null}
 */
export function getSecurityIdentifier(security) {
  if (!security || typeof security !== "object") {
    return null;
  }

  const value = security.id ?? security.security_id;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string" && value.trim().length > 0) {
    return value;
  }

  return null;
}

/**
 * Build the write payload only when the server supplied a real identifier.
 *
 * @param {unknown} security
 * @returns {{security_id: number|string}|null}
 */
export function toAddStockPayload(security) {
  const identifier = getSecurityIdentifier(security);
  return identifier === null ? null : { security_id: identifier };
}

/**
 * The indicators currently persisted by the backend's calculation pipeline.
 * Keep this list in one browser-safe module so the add-column workspace and
 * contract tests share the same vocabulary.
 */
export const WATCH_INDICATOR_OPTIONS = [
  { value: "MA", label: "MA 移动平均" },
  { value: "PROJECTED_MA", label: "Projected MA 下一交易日均线" },
  { value: "RSI", label: "RSI" },
  { value: "KDJ", label: "KDJ" },
  { value: "BOLL", label: "BOLL 布林带" },
  { value: "MACD", label: "MACD" },
  { value: "DIVIDEND_YIELD", label: "股息率" },
];

const DEFAULT_INDICATOR_PARAMETERS = {
  MA: { period: 5 },
  PROJECTED_MA: { period: 5 },
  RSI: { period: 14 },
  KDJ: { period: 9, k_period: 3, d_period: 3 },
  BOLL: { period: 20, multiplier: 2 },
  MACD: { fast_period: 12, slow_period: 26, signal_period: 9 },
  DIVIDEND_YIELD: {},
};

const INDICATOR_FIELD_OPTIONS = {
  BOLL: [
    { value: "upper", label: "Upper 上轨" },
    { value: "middle", label: "Middle 中轨" },
    { value: "lower", label: "Lower 下轨" },
  ],
  KDJ: [
    { value: "k", label: "K" },
    { value: "d", label: "D" },
    { value: "j", label: "J" },
  ],
  MACD: [
    { value: "diff", label: "DIFF" },
    { value: "dea", label: "DEA" },
    { value: "histogram", label: "Histogram 柱" },
  ],
};

function normalizeIndicatorType(value) {
  const normalized = typeof value === "string" ? value.trim().toUpperCase().replace(/[- ]/g, "_") : "";
  if (normalized === "SMA") return "MA";
  if (normalized === "PMA" || normalized === "PROJECTEDMA") return "PROJECTED_MA";
  return normalized;
}

/**
 * Return a fresh copy because callers edit form values before submitting.
 * BOLL deliberately uses `multiplier`, the canonical backend/snapshot key;
 * the API accepts std/stddev aliases but stores this normalized spelling.
 */
export function getDefaultIndicatorParameters(indicatorType) {
  const type = normalizeIndicatorType(indicatorType);
  const defaults = DEFAULT_INDICATOR_PARAMETERS[type];
  return defaults ? { ...defaults } : {};
}

/**
 * Return the explicit scalar fields available when a composite indicator is
 * rendered as NUMBER or DELTA.  The values are the backend's canonical
 * parameter/field vocabulary, not display-only aliases.
 */
export function getIndicatorFieldOptions(indicatorType) {
  const type = normalizeIndicatorType(indicatorType);
  return (INDICATOR_FIELD_OPTIONS[type] ?? []).map((option) => ({ ...option }));
}

/**
 * Build the exact add-column request accepted by the watch-table API.
 */
export function toCreateColumnPayload({ indicatorType, viewMode = "COMPOSITE", parameters }) {
  const normalizedType = normalizeIndicatorType(indicatorType);
  const nextParameters = parameters && typeof parameters === "object"
    ? { ...getDefaultIndicatorParameters(normalizedType), ...parameters }
    : getDefaultIndicatorParameters(normalizedType);

  if (Object.hasOwn(nextParameters, "stddev")) {
    nextParameters.multiplier = nextParameters.stddev;
    delete nextParameters.stddev;
  }

  const normalizedViewMode = typeof viewMode === "string" ? viewMode.toUpperCase() : "COMPOSITE";
  if (normalizedViewMode === "COMPOSITE") {
    delete nextParameters.field;
  } else if (typeof nextParameters.field === "string") {
    nextParameters.field = nextParameters.field.trim().toLowerCase();
  }

  return {
    column_type: "INDICATOR",
    indicator_type: normalizedType,
    parameters: nextParameters,
    view_mode: normalizedViewMode,
  };
}

/**
 * Convert the client-side TanStack order (which contains fixed display
 * columns) into the backend's persisted dynamic-column order request.
 */
export function toColumnOrderPayload(columnOrder) {
  const columnIds = [];
  for (const value of Array.isArray(columnOrder) ? columnOrder : []) {
    const normalized = typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : null;
    if (normalized !== null && Number.isSafeInteger(normalized) && normalized > 0) {
      columnIds.push(normalized);
    }
  }
  return { column_ids: columnIds };
}
