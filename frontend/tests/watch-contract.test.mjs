import test from "node:test";
import assert from "node:assert/strict";

import {
  getDefaultIndicatorParameters,
  getIndicatorFieldOptions,
  getSecurityIdentifier,
  toAddStockPayload,
  toCreateColumnPayload,
  toColumnOrderPayload,
} from "../src/lib/watch-contract.mjs";

test("uses the server-provided security identifier without deriving one from symbol", () => {
  assert.equal(getSecurityIdentifier({ symbol: "600519", name: "贵州茅台" }), null);
  assert.deepEqual(toAddStockPayload({ symbol: "600519", id: 42 }), { security_id: 42 });
  assert.deepEqual(toAddStockPayload({ symbol: "600519", security_id: "42" }), { security_id: "42" });
});

test("rejects empty and non-finite identifiers", () => {
  assert.equal(getSecurityIdentifier({ id: "  " }), null);
  assert.equal(getSecurityIdentifier({ id: Number.NaN }), null);
  assert.equal(toAddStockPayload(null), null);
});

test("indicator defaults use the persistence parameter vocabulary", () => {
  assert.deepEqual(getDefaultIndicatorParameters("MA"), { period: 5 });
  assert.deepEqual(getDefaultIndicatorParameters("RSI"), { period: 14 });
  assert.deepEqual(getDefaultIndicatorParameters("BOLL"), { period: 20, multiplier: 2 });
  assert.deepEqual(getDefaultIndicatorParameters("KDJ"), { period: 9, k_period: 3, d_period: 3 });
  assert.deepEqual(getDefaultIndicatorParameters("MACD"), { fast_period: 12, slow_period: 26, signal_period: 9 });
  assert.deepEqual(getDefaultIndicatorParameters("DIVIDEND_YIELD"), {});
});

test("column payload preserves selected mode and strict indicator parameters", () => {
  assert.deepEqual(
    toCreateColumnPayload({ indicatorType: "boll", viewMode: "COMPOSITE" }),
    {
      column_type: "INDICATOR",
      indicator_type: "BOLL",
      parameters: { period: 20, multiplier: 2 },
      view_mode: "COMPOSITE",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({ indicatorType: "MA", viewMode: "NUMBER", parameters: { period: 20 } }),
    {
      column_type: "INDICATOR",
      indicator_type: "MA",
      parameters: { period: 20 },
      view_mode: "NUMBER",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({
      indicatorType: "BOLL",
      viewMode: "NUMBER",
      parameters: { period: 20, multiplier: 2, field: "upper" },
    }),
    {
      column_type: "INDICATOR",
      indicator_type: "BOLL",
      parameters: { period: 20, multiplier: 2, field: "upper" },
      view_mode: "NUMBER",
    },
  );
  assert.deepEqual(
    toCreateColumnPayload({
      indicatorType: "MACD",
      viewMode: "COMPOSITE",
      parameters: { fast_period: 12, slow_period: 26, signal_period: 9, field: "diff" },
    }).parameters,
    { fast_period: 12, slow_period: 26, signal_period: 9 },
  );
});

test("composite indicator field options use the canonical backend vocabulary", () => {
  assert.deepEqual(getIndicatorFieldOptions("BOLL").map((option) => option.value), ["upper", "middle", "lower"]);
  assert.deepEqual(getIndicatorFieldOptions("KDJ").map((option) => option.value), ["k", "d", "j"]);
  assert.deepEqual(getIndicatorFieldOptions("MACD").map((option) => option.value), ["diff", "dea", "histogram"]);
  assert.deepEqual(getIndicatorFieldOptions("MA"), []);
});

test("column order payload only contains persisted dynamic column ids", () => {
  assert.deepEqual(toColumnOrderPayload(["security", "price", "14", "7", "states"]), {
    column_ids: [14, 7],
  });
  assert.deepEqual(toColumnOrderPayload(["security", "price", "states"]), { column_ids: [] });
});
