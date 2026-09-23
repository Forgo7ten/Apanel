/**
 * Browser-independent authentication decisions.
 *
 * Keeping these decisions outside React makes the security-sensitive edges
 * easy to test without a DOM or a Next.js runtime.
 */

const SAFE_AUTH_MESSAGES = {
  INVALID_CREDENTIALS: "用户名或密码不正确。",
  INVALID_USERNAME_OR_PASSWORD: "用户名或密码不正确。",
  USERNAME_TAKEN: "该用户名已被使用。",
  EMAIL_TAKEN: "该邮箱已被使用。",
  INVALID_INVITATION: "邀请链接无效或已过期。",
  INVITATION_INVALID: "邀请链接无效或已过期。",
  INVITE_INVALID: "邀请链接无效或已过期。",
  INVITATION_EXPIRED: "邀请链接无效或已过期。",
  INVITE_EXPIRED: "邀请链接无效或已过期。",
  UNAUTHORIZED: "登录状态已失效，请重新登录。",
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  NETWORK_ERROR: "暂时无法连接服务，请稍后重试。",
  INVALID_RESPONSE: "服务返回了无法识别的结果，请稍后重试。",
};

/**
 * Read an invitation token from a URL fragment such as #token=abc.
 * Fragments never leave the browser, so this token is intentionally handled
 * only by the registration page and is never written to persistent storage.
 *
 * @param {string} hash
 * @returns {string | null}
 */
export function extractInviteToken(hash) {
  if (typeof hash !== "string" || hash.length === 0) {
    return null;
  }

  const fragment = hash.startsWith("#") ? hash.slice(1) : hash;
  const token = new URLSearchParams(fragment).get("token")?.trim();

  return token || null;
}

/**
 * Remove a URL fragment while preserving the path and query string.
 *
 * @param {string} url
 * @returns {string}
 */
export function stripUrlFragment(url) {
  if (typeof url !== "string") {
    return "";
  }

  const hashIndex = url.indexOf("#");
  return hashIndex === -1 ? url : url.slice(0, hashIndex);
}

/**
 * A request may refresh and retry once after an unauthorized response.
 *
 * @param {number} status
 * @param {boolean} alreadyRetried
 * @returns {boolean}
 */
export function shouldRefreshAfterUnauthorized(status, alreadyRetried) {
  return status === 401 && !alreadyRetried;
}

/**
 * Convert server error codes to deliberately non-sensitive UI copy.
 * Raw response messages are not shown because they may contain implementation
 * details or account-enumeration clues.
 *
 * @param {unknown} code
 * @param {number} [status]
 * @returns {string}
 */
export function safeAuthErrorMessage(code, status) {
  const normalizedCode = typeof code === "string" ? code.toUpperCase() : "";

  if (Object.prototype.hasOwnProperty.call(SAFE_AUTH_MESSAGES, normalizedCode)) {
    return SAFE_AUTH_MESSAGES[normalizedCode];
  }

  if (status === 401) {
    return SAFE_AUTH_MESSAGES.UNAUTHORIZED;
  }

  if (status >= 500) {
    return "服务暂时不可用，请稍后重试。";
  }

  return "操作未完成，请检查输入后重试。";
}
