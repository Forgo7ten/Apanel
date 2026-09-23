import assert from "node:assert/strict";
import test from "node:test";

import {
  extractInviteToken,
  safeAuthErrorMessage,
  shouldRefreshAfterUnauthorized,
  stripUrlFragment,
} from "../src/lib/auth-logic.mjs";

test("registration reads and decodes only the token fragment value", () => {
  assert.equal(extractInviteToken("#token=invite%2Fabc&source=email"), "invite/abc");
  assert.equal(extractInviteToken("#source=email"), null);
  assert.equal(extractInviteToken("#token=%20%20"), null);
});

test("removing the fragment preserves the path and query", () => {
  assert.equal(stripUrlFragment("/register?source=email#token=secret"), "/register?source=email");
  assert.equal(stripUrlFragment("/register"), "/register");
});

test("unauthorized requests can refresh only once", () => {
  assert.equal(shouldRefreshAfterUnauthorized(401, false), true);
  assert.equal(shouldRefreshAfterUnauthorized(401, true), false);
  assert.equal(shouldRefreshAfterUnauthorized(403, false), false);
});

test("auth errors use safe messages instead of server details", () => {
  const leakedDetail = "password hash at /srv/secrets/users.db";

  assert.equal(safeAuthErrorMessage("INVALID_CREDENTIALS", 401), "用户名或密码不正确。");
  assert.equal(safeAuthErrorMessage("UNKNOWN_INTERNAL_CODE", 500), "服务暂时不可用，请稍后重试。");
  assert.equal(safeAuthErrorMessage(leakedDetail, 400), "操作未完成，请检查输入后重试。");
  assert.equal(safeAuthErrorMessage(leakedDetail, 400).includes(leakedDetail), false);
});
