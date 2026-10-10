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

export function orderIndicatorFields(entries) {
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

  const columnValues = isRecord(stock.column_values) ? stock.column_values : {};
  const id = column.id;
  return id === undefined || id === null ? undefined : columnValues[String(id)];
}

/**
 * Read the current composite field envelope, without historic fallbacks.
 */
export function getColumnFields(value) {
  if (!isRecord(value)) return [];
  return isRecord(value.fields) ? orderIndicatorFields(Object.entries(value.fields)) : [];
}

/**
 * Read a field-level scalar without calculating an indicator in the browser.
 */
export function getColumnFieldValue(field, key) {
  if (key === "value") {
    if (typeof field === "number" || typeof field === "string") return field;
    if (isRecord(field)) return field.value ?? null;
    return null;
  }
  if (!isRecord(field)) return null;
  if (key === "delta") return field.delta ?? null;
  return field[key] ?? null;
}
