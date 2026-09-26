/**
 * Shared alert vocabulary. These labels mirror the state IDs exposed by the
 * state engine; the browser only presents them and never evaluates them.
 */
export const ALERT_STATE_OPTIONS = Object.freeze([
  { id: "MA_CROSS_UP", title: "MA 上穿", level: "POSITIVE" },
  { id: "MA_CROSS_DOWN", title: "MA 下穿", level: "NEGATIVE" },
  { id: "MA_GAP_NARROWING", title: "MA 间距收窄", level: "WARNING" },
  { id: "MA_GAP_EXPANDING", title: "MA 间距扩大", level: "INFO" },
  { id: "KDJ_K_CROSS_D_UP", title: "K 上穿 D", level: "POSITIVE" },
  { id: "KDJ_K_CROSS_D_DOWN", title: "K 下穿 D", level: "NEGATIVE" },
  { id: "KDJ_ALL_RISING", title: "KDJ 三线齐上", level: "POSITIVE" },
  { id: "KDJ_ALL_FALLING", title: "KDJ 三线齐下", level: "NEGATIVE" },
  { id: "BOLL_WIDTH_NARROWING", title: "带口收窄", level: "WARNING" },
  { id: "BOLL_WIDTH_EXPANDING", title: "带口扩大", level: "INFO" },
  { id: "BOLL_ALL_RISING", title: "BOLL 三线齐上", level: "POSITIVE" },
  { id: "BOLL_ALL_FALLING", title: "BOLL 三线齐下", level: "NEGATIVE" },
  { id: "BOLL_BREAK_UPPER", title: "突破上轨", level: "POSITIVE" },
  { id: "BOLL_BREAK_LOWER", title: "跌破下轨", level: "NEGATIVE" },
  { id: "MACD_DIFF_CROSS_DEA_UP", title: "DIFF 上穿 DEA", level: "POSITIVE" },
  { id: "MACD_DIFF_CROSS_DEA_DOWN", title: "DIFF 下穿 DEA", level: "NEGATIVE" },
  { id: "MACD_RED_BAR_GROWING", title: "红柱增长", level: "POSITIVE" },
  { id: "MACD_RED_BAR_SHRINKING", title: "红柱缩短", level: "NEGATIVE" },
]);

export const ALERT_INDICATOR_OPTIONS = Object.freeze([
  { id: "RSI", title: "RSI" },
  { id: "MA", title: "MA" },
  { id: "KDJ", title: "KDJ" },
  { id: "BOLL", title: "BOLL" },
  { id: "MACD", title: "MACD" },
  { id: "DIVIDEND_YIELD", title: "股息率" },
]);

export const ALERT_OPERATOR_OPTIONS = Object.freeze([
  { id: ">=", title: "大于等于" },
  { id: ">", title: "大于" },
  { id: "<=", title: "小于等于" },
  { id: "<", title: "小于" },
  { id: "=", title: "等于" },
]);

/**
 * Keep the API boundary canonical and omit fields from the other condition
 * type. This prevents stale form values from being sent during edits.
 *
 * @param {{security_id: number|string, condition_type: "STATE"|"VALUE", state_id?: string, indicator?: string, operator?: string, threshold?: string|number}} form
 * @returns {Record<string, unknown>}
 */
export function buildAlertPayload(form) {
  const payload = {
    security_id: form.security_id,
    condition_type: form.condition_type,
  };

  if (form.condition_type === "STATE") {
    if (!form.state_id?.trim()) {
      throw new Error("请选择状态条件。");
    }
    payload.state_id = form.state_id.trim();
    return payload;
  }

  if (!form.indicator?.trim() || !form.operator?.trim()) {
    throw new Error("请完整填写数值条件。");
  }

  if (typeof form.threshold === "string" && form.threshold.trim() === "") {
    throw new Error("阈值必须是有效数字。");
  }
  const threshold = typeof form.threshold === "number" ? form.threshold : Number(form.threshold);
  if (!Number.isFinite(threshold)) {
    throw new Error("阈值必须是有效数字。");
  }

  payload.indicator = form.indicator.trim();
  payload.operator = form.operator;
  payload.threshold = threshold;
  return payload;
}

/**
 * The backend owns the age/window calculation for PENDING rows.  The browser
 * only trusts the explicit retryable flag and never infers delivery state
 * from provider error text or a webhook URL.
 */
export function shouldShowNotificationRetry(record) {
  if (!record || typeof record !== "object") return false;
  const status = typeof record.status === "string" ? record.status.toUpperCase() : "";
  return status === "FAILED" || (status === "PENDING" && record.retryable === true);
}
