/**
 * Keep the health response independent of Next.js so the contract can be smoke-tested
 * with Node's built-in test runner without adding a test framework to the MVP shell.
 *
 * @param {Date} [now]
 * @returns {{status: "ok", service: "frontend", timestamp: string}}
 */
export function createHealthPayload(now = new Date()) {
  return {
    status: "ok",
    service: "frontend",
    timestamp: now.toISOString(),
  };
}
