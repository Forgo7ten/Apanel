import { INDICATOR_OPTIONS, getDefaultIndicatorParameters, getIndicatorFieldOptions, normalizeIndicatorType } from "./indicator-metadata.mjs";

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
export const WATCH_INDICATOR_OPTIONS = INDICATOR_OPTIONS.map((item) => ({ value: item.value, label: item.label }));
export { getDefaultIndicatorParameters, getIndicatorFieldOptions };

/**
 * Build the exact add-column request accepted by the watch-table API.
 */
export function toCreateColumnPayload({ indicatorType, viewMode = "COMPOSITE", parameters, stateCode }) {
  const normalizedType = normalizeIndicatorType(indicatorType);
  const requestedViewMode = typeof viewMode === "string" ? viewMode.toUpperCase() : "COMPOSITE";
  const normalizedViewMode = normalizedType === "DIVIDEND_YIELD" ? "NUMBER" : requestedViewMode;
  const nextParameters = normalizedViewMode === "STATUS"
    ? (parameters && typeof parameters === "object" ? { ...parameters } : {})
    : parameters && typeof parameters === "object"
      ? { ...getDefaultIndicatorParameters(normalizedType), ...parameters }
      : getDefaultIndicatorParameters(normalizedType);

  if (Object.hasOwn(nextParameters, "stddev")) {
    nextParameters.multiplier = nextParameters.stddev;
    delete nextParameters.stddev;
  }

  if (normalizedViewMode === "COMPOSITE") {
    delete nextParameters.field;
  } else if (typeof nextParameters.field === "string") {
    nextParameters.field = nextParameters.field.trim().toLowerCase();
  }

  const payload = {
    column_type: "INDICATOR",
    indicator_type: normalizedType,
    parameters: nextParameters,
    view_mode: normalizedViewMode,
  };
  if (normalizedViewMode === "STATUS") {
    const normalizedStateCode = typeof stateCode === "string" ? stateCode.trim().toUpperCase() : "";
    if (!normalizedStateCode) throw new Error("STATUS 模式需要选择明确状态。");
    payload.state_code = normalizedStateCode;
  }
  return payload;
}

export function reorderColumnIdsForDrop(ids, sourceId, targetId, position = "before") {
  const order = Array.isArray(ids) ? [...ids] : [];
  const source = String(sourceId);
  const target = String(targetId);
  const sourceIndex = order.findIndex((id) => String(id) === source);
  const targetIndex = order.findIndex((id) => String(id) === target);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return order;
  const [moved] = order.splice(sourceIndex, 1);
  const adjustedTargetIndex = order.findIndex((id) => String(id) === target);
  const insertionIndex = position === "after" ? adjustedTargetIndex + 1 : adjustedTargetIndex;
  order.splice(insertionIndex, 0, moved);
  return order;
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

export const WATCH_DETAIL_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
export const WATCH_BOOTSTRAP_POLL_INTERVAL_MS = 5 * 1000;
export const WATCH_BOOTSTRAP_TIMEOUT_MS = 2 * 60 * 1000;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasPrice(stock) {
  if (!isRecord(stock)) return false;
  const price = stock.price;
  if (typeof price === "number" || typeof price === "string") return price !== "";
  if (!isRecord(price)) return false;
  const value = price.value ?? price.price;
  return (typeof value === "number" && Number.isFinite(value))
    || (typeof value === "string" && value.trim().length > 0);
}

/**
 * A newly added stock is ready for presentation once the price and every
 * currently visible dynamic column have a materialized value.  This is a UI
 * readiness check only; indicator calculation remains entirely on the backend.
 */
export function isWatchStockDataReady(stock, columns = []) {
  if (isRecord(stock) && typeof stock.bootstrap_ready === "boolean") {
    return stock.bootstrap_ready;
  }
  if (!hasPrice(stock)) return false;
  if (!isRecord(stock.column_values)) {
    return !Array.isArray(columns) || columns.filter((column) => column?.visible !== false && column?.hidden !== true).length === 0;
  }

  for (const column of Array.isArray(columns) ? columns : []) {
    if (!column || column.visible === false || column.hidden === true) continue;
    if (column.id === null || column.id === undefined) continue;
    const value = stock.column_values[String(column.id)];
    if (!isRecord(value) || value.available !== true) return false;
  }
  return true;
}

/**
 * Reserve the final row height from column definitions, not from whatever
 * subset of bootstrap data happens to have arrived.  This prevents the whole
 * virtual table from changing height while KDJ/BOLL/MACD fields materialize.
 */
export function expectedCompositeFieldCount(columns = []) {
  let maximum = 0;
  for (const column of Array.isArray(columns) ? columns : []) {
    if (!column || column.visible === false || column.hidden === true) continue;
    if (String(column.view_mode ?? "").toUpperCase() !== "COMPOSITE") continue;
    const indicatorType = normalizeIndicatorType(column.indicator_type ?? column.type);
    const parameters = isRecord(column.parameters) ? column.parameters : {};
    let count = 1;
    if (indicatorType === "MA") {
      const periods = Array.isArray(parameters.periods) ? parameters.periods : [];
      count = periods.length > 0 ? new Set(periods.map((period) => String(period))).size : 1;
    } else {
      count = Math.max(1, getIndicatorFieldOptions(indicatorType).length);
    }
    maximum = Math.max(maximum, count);
  }
  return maximum;
}

export function filterWatchStocks(stocks, { query = "", state = "ALL" } = {}) {
  const items = Array.isArray(stocks) ? stocks : [];
  const normalizedQuery = String(query ?? "").trim().toLocaleLowerCase("zh-CN");
  const normalizedState = String(state ?? "ALL").trim();
  return items.filter((stock) => {
    const name = stock?.name ?? stock?.security?.name ?? "";
    const symbol = stock?.symbol ?? "";
    const matchesQuery = !normalizedQuery
      || String(name).toLocaleLowerCase("zh-CN").includes(normalizedQuery)
      || String(symbol).toLocaleLowerCase("zh-CN").includes(normalizedQuery);
    if (!matchesQuery) return false;
    const states = Array.isArray(stock?.states) ? stock.states : [];
    if (normalizedState === "ALL") return true;
    if (normalizedState === "HAS_STATE") return states.length > 0;
    return states.some((item) => String(item?.state_code ?? item?.state_id ?? "") === normalizedState);
  });
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
export function watchRowLayoutContract(fieldCount = 0, density = "compact") {
  const layout = WATCH_COMPOSITE_LAYOUT;
  const normalizedFieldCount = Math.max(0, Math.floor(Number(fieldCount) || 0));
  const effectiveFieldCount = Math.max(1, normalizedFieldCount);
  const fieldHeight = effectiveFieldCount * layout.fieldBlockHeight
    + (effectiveFieldCount - 1) * layout.fieldGap;
  const stateHeight = layout.stateGap + layout.stateBlockHeight;
  const normalizedDensity = density === "comfortable" ? "comfortable" : "compact";
  const densityPadding = normalizedDensity === "comfortable" ? 16 : 0;
  const contentHeight = layout.verticalPadding + densityPadding + fieldHeight + stateHeight;
  return {
    rowHeight: Math.max(WATCH_DEFAULT_ROW_HEIGHT + densityPadding, Math.ceil(contentHeight / 8) * 8),
    density: normalizedDensity,
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
