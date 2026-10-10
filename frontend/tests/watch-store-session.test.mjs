import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { create } from "zustand";

import { createSessionScope } from "../src/lib/session-scope.mjs";

function setup() {
  const scope = createSessionScope();
  scope.transition("A");
  // Execute the real store module, not a copied model, with an isolated session scope.
  const source = readFileSync(new URL("../src/stores/watch-store.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  runInNewContext(code, {
    exports,
    require(name) {
      if (name === "zustand") return { create };
      if (name === "@/lib/session-scope.mjs") return { sessionScope: scope };
      throw new Error(`Unexpected store dependency: ${name}`);
    },
  });
  return { scope, store: exports.useWatchStore };
}

test("late previous-account actions cannot repopulate a reset Zustand store", () => {
  const { scope, store } = setup();
  const old = store.getState();
  old.setSelectedTableId(10);
  scope.transition("B");
  // This is the reset previously performed by QueryProvider. Resetting values
  // alone is insufficient: callbacks can still hold actions captured by A.
  store.setState({ selectedTableId: null, columnVisibility: {}, columnOrder: [], sorting: [] });
  old.setSelectedTableId(10);
  old.setColumnVisibility({ privateA: true });
  old.setColumnOrder(["privateA"]);
  old.setSorting([{ id: "privateA", desc: true }]);
  assert.equal(store.getState().selectedTableId, null);
  assert.equal(Object.keys(store.getState().columnVisibility).length, 0);
  assert.equal(store.getState().columnOrder.length, 0);
  assert.equal(store.getState().sorting.length, 0);
});

test("session changes renew store actions synchronously, including A to B to A", () => {
  const { scope, store } = setup();
  const firstA = store.getState();
  firstA.setSelectedTableId(10);
  scope.transition("B");
  assert.equal(store.getState().selectedTableId, null);
  store.getState().setSelectedTableId(20);
  scope.transition("A");
  firstA.setSelectedTableId(10);
  assert.equal(store.getState().selectedTableId, null);
  store.getState().setSelectedTableId(30);
  assert.equal(store.getState().selectedTableId, 30);
});

test("same-account token refresh does not invalidate current actions", () => {
  const { scope, store } = setup();
  const current = store.getState();
  scope.transition("A");
  current.setSelectedTableId(10);
  assert.equal(store.getState().selectedTableId, 10);
});

test("an old async callback cannot reset the new account view after await", async () => {
  const { scope, store } = setup();
  const old = store.getState();
  let resolve;
  const delayed = new Promise((done) => { resolve = done; });
  const callback = (async () => { await delayed; old.setSelectedTableId(10); old.resetTableView(); })();
  scope.transition("B");
  store.getState().setSelectedTableId(20);
  store.getState().setColumnOrder(["newB"]);
  resolve();
  await callback;
  assert.equal(store.getState().selectedTableId, 20);
  assert.equal(store.getState().columnOrder[0], "newB");
});
