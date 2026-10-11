import { getDefaultIndicatorParameters, getIndicatorFieldOptions, normalizeIndicatorType } from "./indicator-metadata.mjs";
import { availableMaPeriods } from "./watch-alert-contract.mjs";

/**
 * MA status columns use a short/long pair; numeric columns use period(s).
 * Only an explicit user confirmation may narrow a one/many-period numeric MA
 * to two periods. Converting the other direction preserves the exact pair.
 */
export function maModeNeedsConfirmation(source, targetMode) {
  if (normalizeIndicatorType(source.indicator_type ?? source.type) !== "MA"
      || targetMode !== "STATUS" || source.view_mode === "STATUS") return false;
  return availableMaPeriods(source.parameters ?? {}).length !== 2;
}

export function columnModeParameters(source, targetMode) {
  const indicator = normalizeIndicatorType(source.indicator_type ?? source.type);
  const current = { ...(source.parameters ?? {}) };
  delete current.field; // a field chooses a view, not an indicator variant
  if (indicator !== "MA") {
    // Toggling STATUS is only a display change, never reset user parameters.
    return Object.keys(current).length ? current : getDefaultIndicatorParameters(indicator);
  }
  if (targetMode === "STATUS") {
    if (source.view_mode === "STATUS") return current;
    const periods = availableMaPeriods(current);
    const first = periods[0] ?? 5;
    const second = periods[1] ?? (first < 10_000 ? Math.max(10, first + 1) : first - 1);
    return { short_period: Math.min(first, second), long_period: Math.max(first, second) };
  }
  if (source.view_mode === "STATUS") {
    return { periods: [current.short_period, current.long_period].sort((left, right) => left - right) };
  }
  return Object.keys(current).length ? current : getDefaultIndicatorParameters(indicator);
}

export function columnModeFields(indicatorType, parameters) {
  const indicator = normalizeIndicatorType(indicatorType);
  if (indicator === "MA") return availableMaPeriods(parameters).map((period) => ({ value: `ma${period}`, label: `MA${period}` }));
  return getIndicatorFieldOptions(indicator);
}

/** Match the backend's scalar selector and state parameters before mutation. */
export function buildColumnModeUpdate({ indicatorType, viewMode, parameters, field, stateCode }) {
  const type = normalizeIndicatorType(indicatorType);
  const mode = String(viewMode ?? "").toUpperCase();
  if (!["NUMBER", "DELTA", "STATUS", "COMPOSITE"].includes(mode)) throw new Error("无效展示模式。");
  const normalized = {};
  for (const [key, raw] of Object.entries(parameters ?? {})) {
    if (key === "field") continue;
    if (key === "periods") {
      if (!Array.isArray(raw) || raw.length === 0 || raw.some((period) => !Number.isSafeInteger(period) || period <= 0)) {
        throw new Error("MA 周期必须是正整数。");
      }
      normalized[key] = [...raw];
    } else {
      if (typeof raw === "string" && raw.trim() === "") throw new Error("指标参数不能为空。");
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed <= 0) throw new Error("指标参数必须是正数。");
      if (key.toLowerCase().includes("period") && !Number.isSafeInteger(parsed)) {
        throw new Error("指标周期必须是正整数。");
      }
      normalized[key] = parsed;
    }
  }
  if (mode === "STATUS") {
    if (!stateCode || !String(stateCode).trim()) throw new Error("必须选择状态。");
    if (type === "MA" && normalized.short_period >= normalized.long_period) {
      throw new Error("短周期应小于长周期。");
    }
    return { view_mode: mode, state_code: String(stateCode).trim().toUpperCase(), parameters: normalized };
  }
  if (mode === "NUMBER" || mode === "DELTA") {
    const candidates = columnModeFields(type, normalized);
    const scalar = String(field ?? "").toLowerCase();
    if (candidates.length > 1 || type === "MA") {
      if (!candidates.some((option) => option.value.toLowerCase() === scalar)) {
        throw new Error("请选择当前指标参数对应的字段。");
      }
      normalized.field = scalar;
    } else if (candidates.length === 1) {
      normalized.field = candidates[0].value;
    }
  }
  // Explicit null removes a stale STATUS identity when switching to numeric/composite.
  return { view_mode: mode, state_code: null, parameters: normalized };
}
