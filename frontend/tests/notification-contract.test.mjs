import assert from "node:assert/strict";
import test from "node:test";

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
    }),
    {
      security_id: 7,
      condition_type: "STATE",
      state_id: "BOLL_WIDTH_NARROWING",
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
    }),
    {
      security_id: "42",
      condition_type: "VALUE",
      indicator: "RSI",
      operator: ">=",
      threshold: 70.5,
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
