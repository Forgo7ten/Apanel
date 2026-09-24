import test from "node:test";
import assert from "node:assert/strict";

import { getSecurityIdentifier, toAddStockPayload } from "../src/lib/watch-contract.mjs";

test("uses the server-provided security identifier without deriving one from symbol", () => {
  assert.equal(getSecurityIdentifier({ symbol: "600519", name: "贵州茅台" }), null);
  assert.deepEqual(toAddStockPayload({ symbol: "600519", id: 42 }), { security_id: 42 });
  assert.deepEqual(toAddStockPayload({ symbol: "600519", security_id: "42" }), { security_id: "42" });
});

test("rejects empty and non-finite identifiers", () => {
  assert.equal(getSecurityIdentifier({ id: "  " }), null);
  assert.equal(getSecurityIdentifier({ id: Number.NaN }), null);
  assert.equal(toAddStockPayload(null), null);
});
