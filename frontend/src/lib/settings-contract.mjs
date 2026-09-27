import { INDICATOR_OPTIONS, getDefaultIndicatorParameters } from "./indicator-metadata.mjs";

const DEFAULT_PARAMETERS = Object.freeze(Object.fromEntries(INDICATOR_OPTIONS.map((item) => [item.id, Object.freeze(getDefaultIndicatorParameters(item.id))])));
export const DEFAULT_INDICATORS = Object.freeze(["MA", "RSI", "KDJ", "BOLL", "MACD"]);
export const SETTING_INDICATORS = Object.freeze(INDICATOR_OPTIONS.map((item) => ({ id: item.id, title: item.title })));
const PARAMETER_FIELDS = Object.freeze({
  MA: Object.freeze([{ key: "period", title: "周期" }]),
  PROJECTED_MA: Object.freeze([{ key: "period", title: "周期" }]),
  RSI: Object.freeze([{ key: "period", title: "周期" }]),
  KDJ: Object.freeze([{ key: "period", title: "周期" }, { key: "k_period", title: "K 平滑" }, { key: "d_period", title: "D 平滑" }]),
  BOLL: Object.freeze([{ key: "period", title: "周期" }, { key: "multiplier", title: "标准差倍数" }]),
  MACD: Object.freeze([{ key: "fast_period", title: "快线" }, { key: "slow_period", title: "慢线" }, { key: "signal_period", title: "信号线" }]),
});
export function getParameterFields(indicator) { return PARAMETER_FIELDS[indicator] ?? []; }

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cloneParameters(parameters) {
  const result = {};
  for (const [indicator, defaults] of Object.entries(DEFAULT_PARAMETERS)) {
    result[indicator] = { ...defaults, ...(isRecord(parameters?.[indicator]) ? parameters[indicator] : {}) };
  }
  return result;
}

/**
 * Convert the versioned settings response to the small shape consumed by the
 * form. A configured webhook is deliberately not copied when the server only
 * returns a masked value or a boolean configuration marker.
 */
export function normalizeSettings(input) {
  const source = isRecord(input?.settings) ? input.settings : isRecord(input) ? input : {};
  const indicatorSettings = isRecord(source.indicator_settings) ? source.indicator_settings : isRecord(source.indicators) ? source.indicators : {};
  const displaySettings = isRecord(source.display_settings) ? source.display_settings : isRecord(source.display) ? source.display : {};
  const notificationSettings = isRecord(source.notification_settings) ? source.notification_settings : isRecord(source.notification) ? source.notification : {};
  const rawWebhook = typeof notificationSettings.feishu_webhook === "string" ? notificationSettings.feishu_webhook.trim() : "";
  const maskedWebhook = rawWebhook.length > 0 && /^[*•…]+$/.test(rawWebhook);
  const configured = notificationSettings.feishu_webhook_configured === true || (rawWebhook.length > 0 && !maskedWebhook);
  const defaults = Array.isArray(indicatorSettings.defaults)
    ? indicatorSettings.defaults.filter((value) => typeof value === "string" && value.trim()).map((value) => value.trim().toUpperCase())
    : DEFAULT_INDICATORS.slice();

  return {
    adjustType: source.adjust_type === "none" ? "none" : "qfq",
    defaults: defaults.length > 0 ? defaults : DEFAULT_INDICATORS.slice(),
    parameters: cloneParameters(indicatorSettings.parameters),
    density: displaySettings.density === "comfortable" ? "comfortable" : "compact",
    showStates: displaySettings.show_states !== false,
    showDeltas: displaySettings.show_deltas !== false,
    showMiniChart: displaySettings.show_mini_chart !== false,
    // Never put a server-returned secret into form state. The server should
    // return only feishu_webhook_configured; a user can enter a replacement.
    webhook: "",
    webhookConfigured: configured,
    clearWebhook: false,
  };
}

/**
 * Keep the PUT payload explicit. In particular, an empty webhook is sent as
 * null so users can clear a previously stored secret.
 */
export function toSettingsPayload(form) {
  const payload = {
    adjust_type: form.adjustType,
    indicator_settings: { defaults: [...form.defaults], parameters: form.parameters },
    display_settings: { density: form.density, show_states: form.showStates, show_deltas: form.showDeltas, show_mini_chart: form.showMiniChart },
  };
  const replacement = typeof form.webhook === "string" ? form.webhook.trim() : "";
  if (replacement) payload.notification_settings = { feishu_webhook: replacement };
  else if (form.clearWebhook === true) payload.notification_settings = { feishu_webhook: null };
  return payload;
}
