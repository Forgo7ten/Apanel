import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { QueryClient } from "@tanstack/react-query";
import { createSessionScope, SessionChangedError } from "../src/lib/session-scope.mjs";
import { createAuthStateMessage, isAuthStateMessage } from "../src/lib/auth-coordinator.mjs";

// Node 24 strips TypeScript. Resolve the same aliases as tsconfig so these
// tests execute the real API/session implementation, rather than copying it.
const root = new URL("../src/", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    let candidate;
    if (specifier.startsWith("@/")) candidate = new URL(specifier.slice(2), root);
    else if (specifier.startsWith(".") && context.parentURL?.startsWith(root.href)) {
      candidate = new URL(specifier, context.parentURL);
    }
    if (candidate) {
      if (!existsSync(fileURLToPath(candidate)) && existsSync(fileURLToPath(candidate) + ".ts")) {
        candidate = new URL(candidate.href + ".ts");
      }
      return nextResolve(candidate.href, context);
    }
    return nextResolve(specifier, context);
  },
});
const { apiFetch } = await import("../src/lib/api-client.ts");
const { setSession, clearSession, getSession, login } = await import("../src/lib/auth-session.ts");
const user = (id, token = `token-${id}`) => ({ accessToken: token, user: { id, username: `user-${id}` } });
const envelope = (data, status = 200) => new Response(JSON.stringify({ success: status === 200, data }), { status });
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("account changes invalidate captured scope but token refresh does not", () => {
  const scope = createSessionScope();
  const a = scope.transition("A");
  assert.equal(scope.transition("A"), a);
  assert.doesNotThrow(() => scope.assertCurrent(a));
  scope.transition("B");
  assert.equal(a.signal.aborted, true);
  assert.throws(() => scope.assertCurrent(a), SessionChangedError);
  const b = scope.capture();
  scope.transition(null);
  scope.transition("B");
  assert.throws(() => scope.assertCurrent(b), SessionChangedError);
});

test("old QueryClient cannot supply or repopulate the next account cache", async () => {
  const scope = createSessionScope();
  scope.transition("A");
  const previous = new QueryClient();
  previous.setQueryData(["settings"], { owner: "A" });
  const oldScope = scope.capture();
  scope.transition("B");
  const current = new QueryClient();
  await previous.cancelQueries();
  previous.clear();
  previous.setQueryData(["settings"], { owner: "A-late-callback" });
  const value = await current.fetchQuery({ queryKey: ["settings"], queryFn: () => ({ owner: "B" }), staleTime: 60000 });
  assert.equal(value.owner, "B");
  assert.throws(() => scope.assertCurrent(oldScope), SessionChangedError);
  previous.clear(); current.clear();
});

test("late old-account 401 does not refresh, retry or clear the new account", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; clearSession(); });
  const response = deferred();
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return response.promise; };
  setSession(user("A"));
  const request = apiFetch("/settings");
  const rejected = assert.rejects(request, SessionChangedError);
  setSession(user("B"));
  response.resolve(envelope(null, 401));
  await rejected;
  assert.equal(calls, 1);
  assert.equal(getSession().user.id, "B");
});

test("account switch during JSON body read discards old successful payload", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; clearSession(); });
  const body = deferred();
  const started = deferred();
  globalThis.fetch = async () => ({ status: 200, ok: true, text: async () => { started.resolve(); return body.promise; } });
  setSession(user("A"));
  const request = apiFetch("/settings");
  const rejected = assert.rejects(request, SessionChangedError);
  await started.promise;
  setSession(user("B"));
  body.resolve(JSON.stringify({ success: true, data: { owner: "A" } }));
  await rejected;
  assert.equal(getSession().user.id, "B");
});

test("same-user token refresh still retries with the new access token", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; clearSession(); });
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls += 1;
    if (String(url).endsWith("/auth/refresh")) return envelope({ access_token: "new-token", user: user("A").user });
    if (new Headers(init.headers).get("Authorization") === "Bearer new-token") return envelope({ owner: "A" });
    return envelope(null, 401);
  };
  setSession(user("A", "expired-token"));
  const value = await apiFetch("/settings");
  assert.equal(value.owner, "A");
  assert.equal(getSession().accessToken, "new-token");
  assert.equal(calls, 3);
});

test("logout discards a late login result", async (t) => {
  t.after(clearSession);
  const response = deferred();
  const request = login("user-A", "not-a-real-password", async () => response.promise);
  const rejected = assert.rejects(request, SessionChangedError);
  clearSession();
  response.resolve(envelope({ access_token: "late-token", user: user("A").user }));
  await rejected;
  assert.equal(getSession(), null);
});

test("cross-tab account hint is public identity only, never a credential", () => {
  const message = createAuthStateMessage({ source: "tab-a", epoch: 1, status: "authenticated", userId: "42" });
  assert.equal(isAuthStateMessage(message), true);
  assert.equal(message.userId, "42");
  assert.equal(isAuthStateMessage({ ...message, accessToken: "secret" }), false);
});
