import assert from "node:assert/strict";
import test from "node:test";

import { normalizeSettings, toSettingsPayload } from "../src/lib/settings-contract.mjs";

test("normalizes settings without exposing a masked webhook", () => {
  const form = normalizeSettings({
    adjust_type: "none",
    indicator_settings: { defaults: ["RSI"], parameters: { RSI: { period: 21 } } },
    display_settings: { density: "comfortable", show_states: false },
    notification_settings: { feishu_webhook_configured: true, feishu_webhook: "••••••" },
  });

  assert.equal(form.adjustType, "none");
  assert.deepEqual(form.defaults, ["RSI"]);
  assert.equal(form.parameters.RSI.period, 21);
  assert.equal(form.density, "comfortable");
  assert.equal(form.showStates, false);
  assert.equal(form.webhook, "");
  assert.equal(form.webhookConfigured, true);
});

test("does not copy an unmasked webhook returned by a legacy backend", () => {
  const form = normalizeSettings({ notification_settings: { feishu_webhook: "https://open.feishu.cn/hook/secret" } });

  assert.equal(form.webhook, "");
  assert.equal(form.webhookConfigured, true);
});

test("serializes an empty webhook as null so the backend can clear it", () => {
  const form = normalizeSettings({ notification_settings: { feishu_webhook_configured: true } });
  const payload = toSettingsPayload({ ...form, webhook: "  " });

  assert.equal(payload.notification_settings.feishu_webhook, null);
  assert.equal(Object.hasOwn(payload.notification_settings, "feishu_webhook"), true);
});

test("trims a replacement webhook only at the API boundary", () => {
  const form = normalizeSettings({});
  const payload = toSettingsPayload({ ...form, webhook: " https://open.feishu.cn/hook/example " });

  assert.equal(payload.notification_settings.feishu_webhook, "https://open.feishu.cn/hook/example");
});
