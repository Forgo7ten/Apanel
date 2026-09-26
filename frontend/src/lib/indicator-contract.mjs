const FIELD_ORDER = [
  "upper",
  "middle",
  "lower",
  "width",
  "ma5",
  "ma10",
  "ma20",
  "diff",
  "dea",
  "histogram",
  "rsv",
  "k",
  "d",
  "j",
];

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sortFields(entries) {
  return [...entries].sort(([left], [right]) => {
    const leftIndex = FIELD_ORDER.indexOf(String(left).toLowerCase());
    const rightIndex = FIELD_ORDER.indexOf(String(right).toLowerCase());
    return (leftIndex < 0 ? FIELD_ORDER.length : leftIndex) - (rightIndex < 0 ? FIELD_ORDER.length : rightIndex);
  });
}

/**
 * Read the value for one persisted watch-table column.
 *
 * ``column_values`` is keyed by the database column id.  Once that envelope
 * is present, an absent id is deliberately not allowed to fall through to a
 * same-type value: that would make two MA or BOLL columns render the wrong
 * parameter variant.
 */
export function getColumnValue(stock, column) {
  if (!isRecord(stock) || !isRecord(column)) return undefined;

  if (Object.hasOwn(stock, "column_values")) {
    const columnValues = isRecord(stock.column_values) ? stock.column_values : {};
    const id = column.id;
    if (id === undefined || id === null) return undefined;
    return columnValues[String(id)];
  }

  const keys = [column.key, column.indicator_type, column.type].filter(
    (key) => typeof key === "string" && key.length > 0,
  );
  const sources = [stock.indicators, stock.indicator_values, stock.values];
  for (const source of sources) {
    if (!isRecord(source)) continue;
    for (const key of keys) {
      if (source[key] !== undefined) return source[key];
    }
  }
  return undefined;
}

/**
 * Return the composite fields in deterministic display order.  Older API
 * responses exposed the fields directly on the value, so that shape remains
 * readable during the compatibility window.
 */
export function getColumnFields(value) {
  if (!isRecord(value)) return [];
  if (isRecord(value.fields)) return sortFields(Object.entries(value.fields));

  const reserved = new Set([
    "column_id",
    "view_mode",
    "indicator_type",
    "parameters",
    "available",
    "error_code",
    "value",
    "current_value",
    "previous_value",
    "previous",
    "previous_values",
    "delta",
    "change",
    "direction",
    "state",
    "status",
    "states",
    "title",
    "label",
  ]);
  return sortFields(Object.entries(value).filter(([key]) => !reserved.has(key)));
}

/**
 * Read a field-level scalar without calculating an indicator in the browser.
 */
export function getColumnFieldValue(field, key) {
  if (key === "value") {
    if (typeof field === "number" || typeof field === "string") return field;
    if (isRecord(field)) return field.value ?? field.price ?? field.current ?? null;
    return null;
  }
  if (!isRecord(field)) return null;
  if (key === "delta") return field.delta ?? field.change ?? null;
  return field[key] ?? null;
}
