import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { createHealthPayload } from "../src/lib/health.mjs";

test("frontend health payload exposes a stable service contract", () => {
  const payload = createHealthPayload(new Date("2026-01-02T03:04:05.000Z"));

  assert.deepEqual(payload, {
    status: "ok",
    service: "frontend",
    timestamp: "2026-01-02T03:04:05.000Z",
  });
});

test("workspace header displays the real backend health query instead of placeholder copy", () => {
  const header = readFileSync(new URL("../src/components/layout/Header.tsx", import.meta.url), "utf8");
  const healthApi = readFileSync(new URL("../src/api/health.ts", import.meta.url), "utf8");

  assert.match(header, /queryKey: \["backend-health"\]/);
  assert.match(header, /queryFn: getBackendHealth/);
  assert.match(header, /refetchInterval: HEALTH_REFRESH_INTERVAL_MS/);
  assert.match(header, /后端服务正常/);
  assert.match(header, /后端服务降级/);
  assert.match(header, /后端不可达/);
  assert.doesNotMatch(header, /后端状态待接入/);
  assert.match(healthApi, /response\.ok \|\| response\.status === 503/);
});
