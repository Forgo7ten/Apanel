/**
 * Normalize the two identifier spellings used by securities responses.
 * A missing identifier is intentionally represented as null so the UI cannot
 * invent a security_id from a display symbol.
 *
 * @param {unknown} security
 * @returns {number|string|null}
 */
export function getSecurityIdentifier(security) {
  if (!security || typeof security !== "object") {
    return null;
  }

  const value = security.id ?? security.security_id;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string" && value.trim().length > 0) {
    return value;
  }

  return null;
}

/**
 * Build the write payload only when the server supplied a real identifier.
 *
 * @param {unknown} security
 * @returns {{security_id: number|string}|null}
 */
export function toAddStockPayload(security) {
  const identifier = getSecurityIdentifier(security);
  return identifier === null ? null : { security_id: identifier };
}
