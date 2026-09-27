export const INDICATOR_METADATA = Object.freeze({
  MA: Object.freeze({ title: "MA", parameters: Object.freeze({ period: 5 }), fields: Object.freeze([]), adjustment: true }),
  PROJECTED_MA: Object.freeze({ title: "Projected MA", parameters: Object.freeze({ period: 5 }), fields: Object.freeze([{ value: "value", label: "Value" }]), adjustment: true }),
  RSI: Object.freeze({ title: "RSI", parameters: Object.freeze({ period: 14 }), fields: Object.freeze([{ value: "value", label: "Value" }]), adjustment: true }),
  KDJ: Object.freeze({ title: "KDJ", parameters: Object.freeze({ period: 9, k_period: 3, d_period: 3 }), fields: Object.freeze([{ value: "k", label: "K" }, { value: "d", label: "D" }, { value: "j", label: "J" }]), adjustment: true }),
  BOLL: Object.freeze({ title: "BOLL", parameters: Object.freeze({ period: 20, multiplier: 2 }), fields: Object.freeze([{ value: "upper", label: "Upper 上轨" }, { value: "middle", label: "Middle 中轨" }, { value: "lower", label: "Lower 下轨" }, { value: "width", label: "Width 带宽" }]), adjustment: true }),
  MACD: Object.freeze({ title: "MACD", parameters: Object.freeze({ fast_period: 12, slow_period: 26, signal_period: 9 }), fields: Object.freeze([{ value: "diff", label: "DIFF" }, { value: "dea", label: "DEA" }, { value: "histogram", label: "Histogram 柱" }]), adjustment: true }),
  DIVIDEND_YIELD: Object.freeze({ title: "股息率", parameters: Object.freeze({}), fields: Object.freeze([{ value: "value", label: "Value" }]), adjustment: false }),
});

export const INDICATOR_OPTIONS = Object.freeze(Object.entries(INDICATOR_METADATA).map(([id, value]) => ({ id, value: id, title: value.title, label: value.title })));

export function normalizeIndicatorType(value) {
  const normalized = typeof value === "string" ? value.trim().toUpperCase().replace(/[- ]/g, "_") : "";
  if (normalized === "SMA") return "MA";
  if (normalized === "PMA" || normalized === "PROJECTEDMA") return "PROJECTED_MA";
  return normalized;
}

export function getDefaultIndicatorParameters(indicatorType) {
  const metadata = INDICATOR_METADATA[normalizeIndicatorType(indicatorType)];
  return metadata ? { ...metadata.parameters } : {};
}

export function getIndicatorFieldOptions(indicatorType) {
  const metadata = INDICATOR_METADATA[normalizeIndicatorType(indicatorType)];
  return metadata ? metadata.fields.map((item) => ({ ...item })) : [];
}

export function indicatorUsesAdjustment(indicatorType) {
  return INDICATOR_METADATA[normalizeIndicatorType(indicatorType)]?.adjustment !== false;
}
