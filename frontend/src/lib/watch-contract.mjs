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

/**
 * Keep stale detail data visible while a background request is in flight.
 * The UI uses these states to distinguish the first load from a refresh and
 * to avoid replacing a usable table with a blocking error panel.
 */
export function watchDetailViewState({ isPending = false, isFetching = false, isError = false, hasData = false } = {}) {
  if (hasData && isError) return "refresh-error";
  if (hasData && isFetching) return "refreshing";
  if (hasData) return "ready";
  if (isPending) return "loading";
  if (isError) return "error";
  return "empty";
}

/**
 * Safe, non-diagnostic copy shared by route error boundaries.
 * Never include an exception message in a user-facing fallback.
 */
export function errorBoundaryCopy() {
  return {
    title: "页面暂时不可用",
    description: "页面加载遇到问题，请重试。",
    retryLabel: "重新加载",
  };
}

// Composite values are not limited to a static field count: MA bundles can
// contain any number of requested periods, and indicator contracts may grow.
// The table computes one row height from the largest composite value currently
// present, while this default keeps an empty/small table usable.
export const WATCH_DEFAULT_ROW_HEIGHT = 72;
export const WATCH_COMPOSITE_LAYOUT = Object.freeze({
  fieldBlockHeight: 32,
  fieldGap: 2,
  maxStateTags: 2,
  stateBlockHeight: 20,
  stateGap: 4,
  verticalPadding: 8,
});

/**
 * Calculate the one fixed row height used by every row in the current table.
 * ``fieldCount`` is the maximum number of fields among all composite cells;
 * each cell still renders all of its own fields.  Rounding to an 8px grid is
 * a layout safety margin for font metrics while preserving contentHeight <=
 * rowHeight as an explicit invariant.
 */
export function watchRowLayoutContract(fieldCount = 0) {
  const layout = WATCH_COMPOSITE_LAYOUT;
  const normalizedFieldCount = Math.max(0, Math.floor(Number(fieldCount) || 0));
  const effectiveFieldCount = Math.max(1, normalizedFieldCount);
  const fieldHeight = effectiveFieldCount * layout.fieldBlockHeight
    + (effectiveFieldCount - 1) * layout.fieldGap;
  const stateHeight = layout.stateGap + layout.stateBlockHeight;
  const contentHeight = layout.verticalPadding + fieldHeight + stateHeight;
  return {
    rowHeight: Math.max(WATCH_DEFAULT_ROW_HEIGHT, Math.ceil(contentHeight / 8) * 8),
    contentHeight,
    fieldCount: normalizedFieldCount,
    effectiveFieldCount,
    ...layout,
  };
}

/**
 * Do not reuse a previous query's placeholder when it belongs to another
 * selected watch table.
 */
export function isCurrentWatchDetail(detail, activeTableId) {
  if (activeTableId === null || activeTableId === undefined) return false;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return false;
  const detailId = detail.id;
  return detailId !== null && detailId !== undefined && String(detailId) === String(activeTableId);
}

/**
 * Calculate an exclusive row range for a fixed-height, overscanned viewport.
 * ``end - start`` is intentionally small for large tables while small tables
 * remain fully rendered for predictable keyboard and screen-reader behavior.
 */
export function calculateVirtualRange({
  itemCount = 0,
  scrollTop = 0,
  viewportHeight = 0,
  rowHeight = WATCH_DEFAULT_ROW_HEIGHT,
  overscan = 4,
} = {}) {
  const count = Math.max(0, Math.floor(Number(itemCount) || 0));
  const height = Math.max(1, Number(viewportHeight) || 1);
  const row = Math.max(1, Number(rowHeight) || 1);
  const extra = Math.max(0, Math.floor(Number(overscan) || 0));
  const visibleCount = Math.max(1, Math.ceil(height / row));
  const totalHeight = count * row;
  if (count === 0) return { start: 0, end: 0, offsetTop: 0, offsetBottom: 0, totalHeight };
  if (count <= visibleCount + extra * 2) {
    return { start: 0, end: count, offsetTop: 0, offsetBottom: 0, totalHeight };
  }

  const safeScrollTop = Math.max(0, Number(scrollTop) || 0);
  const firstVisible = Math.min(count - 1, Math.floor(safeScrollTop / row));
  const start = Math.max(0, firstVisible - extra);
  const bottomStart = Math.max(0, count - visibleCount - extra);
  const boundedStart = Math.min(start, bottomStart);
  const boundedEnd = Math.min(count, boundedStart + visibleCount + extra * 2);
  return {
    start: boundedStart,
    end: boundedEnd,
    offsetTop: boundedStart * row,
    offsetBottom: Math.max(0, totalHeight - boundedEnd * row),
    totalHeight,
  };
}

/**
 * Return a target index only when focus should wrap or enter the dialog.
 * ``-1`` means native Tab order can continue without interception.
 */
export function dialogFocusTargetIndex(currentIndex, count, shiftKey = false) {
  if (!Number.isInteger(count) || count <= 0) return -1;
  if (!Number.isInteger(currentIndex) || currentIndex < 0) return shiftKey ? count - 1 : 0;
  if (shiftKey && currentIndex === 0) return count - 1;
  if (!shiftKey && currentIndex === count - 1) return 0;
  return -1;
}

export function virtualScrollTopForKey(
  key,
  currentScrollTop = 0,
  viewportHeight = 0,
  totalHeight = 0,
  rowHeight = WATCH_DEFAULT_ROW_HEIGHT,
) {
  const maxScrollTop = Math.max(0, Number(totalHeight) - Number(viewportHeight));
  const current = Math.min(maxScrollTop, Math.max(0, Number(currentScrollTop) || 0));
  const row = Math.max(1, Number(rowHeight) || 1);
  switch (key) {
    case "ArrowDown":
      return Math.min(maxScrollTop, current + row);
    case "ArrowUp":
      return Math.max(0, current - row);
    case "PageDown":
      return Math.min(maxScrollTop, current + Math.max(1, Number(viewportHeight) || row));
    case "PageUp":
      return Math.max(0, current - Math.max(1, Number(viewportHeight) || row));
    case "Home":
      return 0;
    case "End":
      return maxScrollTop;
    default:
      return null;
  }
}
