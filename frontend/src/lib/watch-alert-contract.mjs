import { getDefaultIndicatorParameters, indicatorUsesAdjustment, normalizeIndicatorType } from "./indicator-metadata.mjs";

/** Preserve exact parameter/field/adjustment identity from Watch cells. */
export function buildDetailAlertDraft(selection) {
  const { stock } = selection;
  const id = stock.security_id ?? stock.security?.security_id ?? stock.security?.id;
  if (id == null) throw new Error("缺少证券编号。");
  const security = { id, security_id: id, symbol: stock.symbol, name: stock.name ?? stock.symbol, market: stock.market ?? "" };
  if (selection.kind === "state") {
    return {
      security, condition_type: "STATE",
      state_id: selection.state.state_code ?? selection.state.state_id,
      parameters: numericParameters(selection.state.parameters ?? {}),
      adjust_type: selection.state.adjust_type === "none" ? "none" : "qfq",
    };
  }
  const raw = selection.column.parameters ?? {};
  const indicator = normalizeIndicatorType(selection.column.indicator_type ?? selection.column.type);
  if (!indicator) throw new Error("缺少指标类型。");
  const parameters = numericParameters(raw);
  let field = typeof raw.field === "string" ? raw.field : undefined;
  if (indicator === "MA" && !field) {
    const periods = maPeriods(parameters);
    if (periods.length === 1) field = "MA" + periods[0];
  }
  return {
    security, condition_type: "VALUE", indicator, parameters, field,
    adjust_type: indicatorUsesAdjustment(indicator) ? selection.adjust_type : "none",
  };
}

export function numericParameters(raw) {
  const result = {};
  for (const [key, value] of Object.entries(raw ?? {})) {
    if (key === "field") continue;
    if (typeof value === "number" && Number.isFinite(value)) {
      result[key] = value;
    } else if (key === "periods" && Array.isArray(value) && value.length > 0
      && value.every((period) => Number.isSafeInteger(period) && period > 0)) {
      result[key] = [...value];
    } else {
      throw new Error("指标参数不完整，请重新选择监控列。");
    }
  }
  return result;
}

export function maPeriods(parameters = {}) {
  if ("period" in parameters && "periods" in parameters) {
    throw new Error("MA 单周期和多周期参数不能同时设置。");
  }
  const periods = parameters.periods ?? (parameters.period === undefined
    ? [getDefaultIndicatorParameters("MA").period] : [parameters.period]);
  if (!Array.isArray(periods) || periods.length === 0
    || periods.some((value) => !Number.isSafeInteger(value) || value <= 0)) {
    throw new Error("MA 周期必须为正整数。");
  }
  return [...new Set(periods)].sort((a, b) => a - b);
}

/** Never throw while a user is editing an incomplete MA period input. */
export function availableMaPeriods(parameters) {
  try {
    return maPeriods(parameters);
  } catch {
    return [];
  }
}

export function initialValueParameters(indicator, supplied) {
  const own = supplied && Object.keys(supplied).length > 0 ? supplied : getDefaultIndicatorParameters(indicator);
  return numericParameters(own);
}
