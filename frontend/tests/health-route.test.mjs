import assert from "node:assert/strict";
import test from "node:test";

import { createHealthPayload } from "../src/lib/health.mjs";

test("frontend health payload exposes a stable service contract", () => {
  const payload = createHealthPayload(new Date("2026-01-02T03:04:05.000Z"));

  assert.deepEqual(payload, {
    status: "ok",
    service: "frontend",
    timestamp: "2026-01-02T03:04:05.000Z",
  });
});
