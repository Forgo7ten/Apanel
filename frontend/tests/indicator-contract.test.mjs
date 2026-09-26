import test from "node:test";
import assert from "node:assert/strict";

import {
  getColumnFieldValue,
  getColumnFields,
  getColumnValue,
} from "../src/lib/indicator-contract.mjs";

const ma5 = {
  column_id: 101,
  view_mode: "NUMBER",
  indicator_type: "MA",
  parameters: { period: 5 },
  available: true,
  value: 105,
  previous_value: 104,
  delta: 1,
  direction: "UP",
};

const ma20 = {
  column_id: 102,
  view_mode: "NUMBER",
  indicator_type: "MA",
  parameters: { period: 20 },
  available: true,
  value: 120,
  previous_value: 117,
  delta: 3,
  direction: "UP",
};

test("column id is the primary key when same-type columns coexist", () => {
  const stock = {
    column_values: { "101": ma5, "102": ma20 },
    indicators: { MA: { value: 999 } },
  };

  assert.equal(getColumnValue(stock, { id: 101, indicator_type: "MA", type: "MA" }), ma5);
  assert.equal(getColumnValue(stock, { id: 102, indicator_type: "MA", type: "MA" }), ma20);
  assert.equal(getColumnValue(stock, { id: 103, indicator_type: "MA", type: "MA" }), undefined);
});

test("different BOLL parameter columns keep their own composite fields", () => {
  const boll20x2 = {
    column_id: 201,
    view_mode: "COMPOSITE",
    indicator_type: "BOLL",
    parameters: { period: 20, multiplier: 2 },
    available: true,
    fields: { upper: { value: 110, previous_value: 109, delta: 1, direction: "UP" } },
  };
  const boll20x3 = {
    ...boll20x2,
    column_id: 202,
    parameters: { period: 20, multiplier: 3 },
    fields: { upper: { value: 115, previous_value: 114, delta: 1, direction: "UP" } },
  };
  const stock = { column_values: { "201": boll20x2, "202": boll20x3 } };

  assert.equal(getColumnValue(stock, { id: 201, indicator_type: "BOLL" }).parameters.multiplier, 2);
  assert.equal(getColumnValue(stock, { id: 202, indicator_type: "BOLL" }).parameters.multiplier, 3);
});

test("legacy responses fall back to indicator type only when column_values is absent", () => {
  const legacy = { indicators: { MA: ma20 } };
  assert.equal(getColumnValue(legacy, { id: 101, indicator_type: "MA", type: "MA" }), ma20);

  const partial = { column_values: {}, indicators: { MA: ma20 } };
  assert.equal(getColumnValue(partial, { id: 101, indicator_type: "MA", type: "MA" }), undefined);
});

test("composite fields preserve scalar value, previous value, delta and direction", () => {
  const boll = {
    column_id: 201,
    view_mode: "COMPOSITE",
    indicator_type: "BOLL",
    available: true,
    fields: {
      upper: { value: 110, previous_value: 109, delta: 1, direction: "UP" },
      middle: { value: 100, previous_value: 100, delta: 0, direction: "FLAT" },
    },
  };

  assert.deepEqual(getColumnFields(boll), [
    ["upper", boll.fields.upper],
    ["middle", boll.fields.middle],
  ]);
  assert.equal(getColumnFieldValue(boll.fields.upper, "value"), 110);
  assert.equal(getColumnFieldValue(boll.fields.upper, "previous_value"), 109);
  assert.equal(getColumnFieldValue(boll.fields.upper, "delta"), 1);
  assert.equal(getColumnFieldValue(boll.fields.upper, "direction"), "UP");
});

test("scalar NUMBER and DELTA payloads are read without browser-side calculation", () => {
  const scalar = { value: 71, previous_value: 69, delta: 2, direction: "UP" };
  assert.equal(getColumnFieldValue(scalar, "value"), 71);
  assert.equal(getColumnFieldValue(scalar, "previous_value"), 69);
  assert.equal(getColumnFieldValue(scalar, "delta"), 2);
  assert.equal(getColumnFieldValue(scalar, "direction"), "UP");
});
