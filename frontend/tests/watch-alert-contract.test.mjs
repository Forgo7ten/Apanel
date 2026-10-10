import assert from "node:assert/strict";
import test from "node:test";

import { buildDetailAlertDraft, initialValueParameters, maPeriods, availableMaPeriods } from "../src/lib/watch-alert-contract.mjs";
import { buildAlertPayload } from "../src/lib/notification-contract.mjs";

const stock = { security_id: 17, symbol: "600519", name: "样本", market: "SH" };
const indicator = (type, parameters, adjust_type = "none") => ({
  kind: "indicator", stock, adjust_type, column: { indicator_type: type, parameters },
});

test("Watch VALUE keeps none/qfq adjustment, field and RSI/BOLL parameter identities", () => {
  for (const adjust_type of ["none", "qfq"]) {
    const draft = buildDetailAlertDraft(indicator("RSI", { period: 6, field: "value" }, adjust_type));
    assert.equal(draft.adjust_type, adjust_type);
    assert.deepEqual(draft.parameters, { period: 6 });
    const payload = buildAlertPayload({ ...draft, operator: ">=", threshold: 70 });
    assert.equal(payload.adjust_type, adjust_type);
    assert.deepEqual(payload.parameters, { period: 6 });
    assert.equal(payload.field, "value");
  }
  const boll = buildDetailAlertDraft(indicator("BOLL", { period: 21, multiplier: 2.5, field: "upper" }));
  assert.deepEqual(boll.parameters, { period: 21, multiplier: 2.5 });
  assert.equal(buildAlertPayload({ ...boll, operator: ">", threshold: 3 }).field, "upper");
});

test("MA multi-period column carries complete periods without adding the default period", () => {
  const source = { periods: [20, 60], field: "MA20" };
  const draft = buildDetailAlertDraft(indicator("MA", source));
  assert.deepEqual(draft.parameters, { periods: [20, 60] });
  assert.deepEqual(initialValueParameters("MA", draft.parameters), { periods: [20, 60] });
  assert.deepEqual(maPeriods(draft.parameters), [20, 60]);
  const payload = buildAlertPayload({ ...draft, threshold: 10, operator: ">" });
  assert.equal(payload.field, "MA20");
  assert.equal(payload.adjust_type, "none");
  assert.deepEqual(payload.parameters, { periods: [20, 60] });
  assert.deepEqual(source, { periods: [20, 60], field: "MA20" });
  assert.throws(
    () => buildAlertPayload({ ...draft, field: "MA5", threshold: 10, operator: ">" }),
    /MA 字段/,
  );
  assert.throws(() => maPeriods({ period: 5, periods: [20, 60] }), /不能同时/);
});

test("MA single period picks exact scalar field, composite requires explicit choice", () => {
  assert.equal(buildDetailAlertDraft(indicator("MA", { period: 20 })).field, "MA20");
  const composite = buildDetailAlertDraft(indicator("MA", { periods: [20, 60] }));
  assert.equal(composite.field, undefined);
  assert.throws(
    () => buildAlertPayload({ ...composite, operator: ">", threshold: 30 }),
    /请选择具体指标字段/,
  );
});

test("state alert keeps state-specific parameters, dividend yield is non-adjusted", () => {
  const state = buildDetailAlertDraft({
    kind: "state", stock, state: { state_code: "MA_CROSS_UP", adjust_type: "none", parameters: { short_period: 20, long_period: 60 } },
  });
  assert.deepEqual(buildAlertPayload({ ...state, security_id: state.security.security_id }), {
    security_id: 17, condition_type: "STATE", state_id: "MA_CROSS_UP",
    parameters: { short_period: 20, long_period: 60 }, adjust_type: "none",
  });
  const dividend = buildDetailAlertDraft(indicator("DIVIDEND_YIELD", { field: "value" }, "qfq"));
  assert.equal(dividend.adjust_type, "none");
  assert.equal(buildAlertPayload({ ...dividend, security_id: dividend.security.security_id, operator: ">", threshold: 0.03 }).adjust_type, undefined);
});

test("malformed period lists fail closed instead of silently becoming MA5", () => {
  assert.throws(() => buildDetailAlertDraft(indicator("MA", { periods: ["20", 60], field: "MA20" })), /不完整/);
  assert.throws(() => buildDetailAlertDraft(indicator("MA", { periods: [], field: "MA20" })), /不完整/);
});

test("in-progress invalid MA input cannot crash the field picker", () => {
  assert.deepEqual(availableMaPeriods({ period: 0 }), []);
  assert.deepEqual(availableMaPeriods({ period: 20, periods: [5] }), []);
  assert.deepEqual(availableMaPeriods({ periods: [20, 60] }), [20, 60]);
  assert.throws(
    () => buildAlertPayload({ security_id: 17, condition_type: "VALUE", indicator: "MA", parameters: { period: 0 }, field: "MA20", operator: ">", threshold: 1 }),
    /MA/,
  );
});
