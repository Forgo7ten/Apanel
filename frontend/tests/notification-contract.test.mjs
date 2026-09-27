import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  ALERT_STATE_OPTIONS,
  buildAlertPayload,
  shouldShowNotificationRetry,
} from "../src/lib/notification-contract.mjs";

test("alert vocabulary keeps backend state IDs and user-facing titles together", () => {
  const narrowing = ALERT_STATE_OPTIONS.find((option) => option.id === "BOLL_WIDTH_NARROWING");

  assert.deepEqual(narrowing, {
    id: "BOLL_WIDTH_NARROWING",
    title: "带口收窄",
    level: "WARNING",
  });
});

test("state alert payload omits stale value fields", () => {
  assert.deepEqual(
    buildAlertPayload({
      security_id: 7,
      condition_type: "STATE",
      state_id: "BOLL_WIDTH_NARROWING",
      indicator: "RSI",
      operator: ">=",
      threshold: "70",
      adjust_type: "qfq",
    }),
    {
      security_id: 7,
      condition_type: "STATE",
      state_id: "BOLL_WIDTH_NARROWING",
      adjust_type: "qfq",
    },
  );
});


test("state alert payload preserves exact state parameters", () => {
  assert.deepEqual(
    buildAlertPayload({
      security_id: 1,
      condition_type: "STATE",
      state_id: "MA_CROSS_UP",
      parameters: { short_period: 20, long_period: 60 },
      adjust_type: "none",
    }),
    {
      security_id: 1,
      condition_type: "STATE",
      state_id: "MA_CROSS_UP",
      parameters: { short_period: 20, long_period: 60 },
      adjust_type: "none",
    },
  );
});

test("value alert payload serializes numeric thresholds", () => {
  assert.deepEqual(
    buildAlertPayload({
      security_id: "42",
      condition_type: "VALUE",
      indicator: "RSI",
      operator: ">=",
      threshold: "70.5",
      adjust_type: "none",
    }),
    {
      security_id: "42",
      condition_type: "VALUE",
      indicator: "RSI",
      operator: ">=",
      threshold: 70.5,
      parameters: { period: 14 },
      field: "value",
      adjust_type: "none",
    },
  );
});

test("value alerts reject non-numeric thresholds without echoing input", () => {
  assert.throws(
    () => buildAlertPayload({ security_id: 1, condition_type: "VALUE", indicator: "RSI", operator: ">=", threshold: "secret" }),
    /阈值必须是有效数字/,
  );
  assert.throws(
    () => buildAlertPayload({ security_id: 1, condition_type: "VALUE", indicator: "RSI", operator: ">=", threshold: "  " }),
    /阈值必须是有效数字/,
  );
});


test("composite value alerts require an explicit scalar field", () => {
  assert.throws(
    () => buildAlertPayload({ security_id: 1, condition_type: "VALUE", indicator: "MACD", operator: ">=", threshold: "1", parameters: { fast_period: 12, slow_period: 26, signal_period: 9 } }),
    /请选择具体指标字段/,
  );
  const payload = buildAlertPayload({ security_id: 1, condition_type: "VALUE", indicator: "MACD", operator: ">=", threshold: "1", parameters: { fast_period: 12, slow_period: 26, signal_period: 9 }, field: "histogram", adjust_type: "qfq" });
  assert.equal(payload.field, "histogram");
  assert.deepEqual(payload.parameters, { fast_period: 12, slow_period: 26, signal_period: 9 });
});

test("notification retry action is available for failed and server-marked recoverable pending rows", () => {
  assert.equal(shouldShowNotificationRetry({ status: "FAILED" }), true);
  assert.equal(shouldShowNotificationRetry({ status: "PENDING", retryable: true }), true);
  assert.equal(shouldShowNotificationRetry({ status: "PENDING", retryable: false }), false);
  assert.equal(shouldShowNotificationRetry({ status: "SENT", retryable: true }), false);
});

test("notification retry action never derives retryability from unsafe error text", () => {
  assert.equal(
    shouldShowNotificationRetry({
      status: "PENDING",
      retryable: false,
      error_message: "https://open.feishu.cn/open-apis/bot/v2/hook/secret",
    }),
    false,
  );
});

test("alert wizard only shows security-search loading while an unselected search is fetching", () => {
  const source = readFileSync(new URL("../src/components/alerts/AlertWizard.tsx", import.meta.url), "utf8");
  assert.match(source, /!selectedSecurity && searchQuery\.isFetching/);
  assert.doesNotMatch(source, /\{searchQuery\.isPending \? <p[^>]*>正在搜索证券/);
});
