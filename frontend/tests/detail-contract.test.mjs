import assert from "node:assert/strict";
import test from "node:test";

import {
  buildHistoryPath,
  detailViewState,
  focusLoopIndex,
  historyWindow,
  isValidDetailSelection,
  isDetailActivationKey,
  isDetailCloseKey,
  miniChartPointLabel,
  miniChartGeometry,
  matchesHistoryParameters,
  selectStateHistory,
  selectRelatedStates,
  toMiniChartPoints,
} from "../src/lib/detail-contract.mjs";

test("history requests use one stable date-range envelope", () => {
  assert.equal(
    buildHistoryPath("/securities/600519/indicators/history", {
      start: "2026-01-01",
      end: "2026-03-31",
      adjust: "qfq",
      parameter_key: "v1_rsi6",
    }),
    "/securities/600519/indicators/history?start=2026-01-01&end=2026-03-31&adjust=qfq&parameter_key=v1_rsi6",
  );
  assert.deepEqual(historyWindow(new Date("2026-03-31T12:00:00Z")), { start: "2026-01-01", end: "2026-03-31" });
});

test("mini chart handles empty, single-point, and multi-point calculated values", () => {
  const column = { indicator_type: "RSI", parameters: { period: 6 }, parameter_key: "rsi6" };
  const items = [
    { trade_date: "2026-01-01", indicator_type: "RSI", parameter_key: "rsi6", parameters: { period: 6 }, values: { value: 40 } },
    { trade_date: "2026-01-02", indicator_type: "RSI", parameter_key: "rsi6", parameters: { period: 6 }, values: { value: 42 } },
  ];
  assert.deepEqual(toMiniChartPoints([], column), []);
  const single = [{ date: "2026-01-01", value: 40 }];
  assert.equal(miniChartGeometry(single).length, 1);
  assert.equal(miniChartPointLabel(single[0]), "2026-01-01：40");
  assert.equal(toMiniChartPoints(items, column).length, 2);
  assert.equal(miniChartGeometry(toMiniChartPoints(items, column)).length, 2);
});

test("persisted parameter key is primary and legacy matching is exact", () => {
  const column = { indicator_type: "RSI", parameters: { period: 6 }, parameter_key: "rsi6" };
  const matching = { indicator_type: "RSI", parameter_key: "rsi6", parameters: { period: 14 }, values: { value: 80 } };
  const wrongKey = { ...matching, parameter_key: "rsi14", parameters: { period: 6 } };
  assert.equal(matchesHistoryParameters(matching, column, "rsi6"), true);
  assert.equal(matchesHistoryParameters(wrongKey, column, "rsi6"), false);
  assert.equal(matchesHistoryParameters({ indicator_type: "RSI", parameters: { period: 6 } }, column), true);
  assert.equal(matchesHistoryParameters({ indicator_type: "RSI", parameters: { period: 14 } }, column), false);
});

test("state history is selected by state_code and detail selection is discriminated", () => {
  const items = [
    { state_id: "A", state_code: "A", title: "A", trade_date: "2026-01-01" },
    { state_id: "B", state_code: "B", title: "B", trade_date: "2026-01-01" },
  ];
  assert.deepEqual(selectStateHistory(items, "B"), [items[1]]);
  assert.equal(isValidDetailSelection({ kind: "indicator", stock: {}, column: {} }), true);
  assert.equal(isValidDetailSelection({ kind: "state", stock: {}, state: {} }), true);
  assert.equal(isValidDetailSelection({ kind: "indicator", stock: {}, column: {}, state: {} }), false);
  assert.equal(isValidDetailSelection({ stock: {} }), false);
  assert.deepEqual(
    selectRelatedStates([
      { state_code: "RSI_OVER", indicator_type: "RSI" },
      { state_code: "MACD_UP", indicator_type: "MACD" },
    ], "RSI"),
    [{ state_code: "RSI_OVER", indicator_type: "RSI" }],
  );
  assert.deepEqual(selectRelatedStates([], "RSI"), []);
});

test("drawer keyboard and query states have explicit contracts", () => {
  assert.equal(isDetailActivationKey("Enter"), true);
  assert.equal(isDetailActivationKey(" "), true);
  assert.equal(isDetailCloseKey("Escape"), true);
  assert.equal(focusLoopIndex(0, 3, true), 2);
  assert.equal(focusLoopIndex(2, 3, false), 0);
  assert.equal(detailViewState({ loading: true, itemCount: 0 }), "loading");
  assert.equal(detailViewState({ error: true, itemCount: 0 }), "error");
  assert.equal(detailViewState({ itemCount: 0 }), "empty");
  assert.equal(detailViewState({ itemCount: 2 }), "ready");
});
