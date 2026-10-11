import { getDefaultIndicatorParameters, normalizeIndicatorType } from "./indicator-metadata.mjs";
import { availableMaPeriods } from "./watch-alert-contract.mjs";

function calculateParameters(indicator, raw = {}) {
  const params = { ...getDefaultIndicatorParameters(indicator), ...raw };
  delete params.field;
  return params;
}

/** Match a persisted state to one column, not just an indicator type. */
export function stateMatchesIndicatorColumn(state, column) {
  if (!state || !column) return false;
  const indicator = normalizeIndicatorType(column.indicator_type ?? column.type);
  if (!indicator || indicator !== normalizeIndicatorType(state.indicator_type)) return false;
  if (column.view_mode === "STATUS" && column.state_code
      && String(state.state_code ?? state.state_id).toUpperCase() !== column.state_code.toUpperCase()) return false;
  const actual = state.parameters ?? {};
  if (indicator === "MA") {
    if (column.view_mode === "STATUS") {
      const expected = column.parameters ?? {};
      return Number(actual.short_period) === Number(expected.short_period)
        && Number(actual.long_period) === Number(expected.long_period);
    }
    const periods = availableMaPeriods(column.parameters ?? {});
    const relevantPair = periods.length === 2
      ? periods
      : periods.length > 2 && periods.includes(5) && periods.includes(10) ? [5, 10] : [];
    return relevantPair.length === 2
      && Number(actual.short_period) === relevantPair[0]
      && Number(actual.long_period) === relevantPair[1];
  }
  const expected = calculateParameters(indicator, column.parameters ?? {});
  const received = calculateParameters(indicator, actual);
  return Object.keys(expected).length === Object.keys(received).length
    && Object.keys(expected).every((key) => Number(expected[key]) === Number(received[key]));
}

export function selectColumnStateHistory(items, column) {
  return (Array.isArray(items) ? items : []).filter((item) => stateMatchesIndicatorColumn(item, column));
}
